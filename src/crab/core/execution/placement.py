"""Which cores each co-located app gets, and the environment each app's ranks run with (ADR-033).

An app's reserved `cpus` key is a core list ("0-15,32", one core per rank) and `env` is an object
of variables for its ranks. Apps whose nodes overlap run at the same time on the same hosts
(`allocation.mode: "shared"`); `cores: "disjoint"` (the default there) keeps them on different
cores, `cores: "shared"` lets them compete for the same ones.

Pure apart from `os.cpu_count`, read only for a bare `localhost` preset host. Every error is a
`ValueError`; the experiment-level entry points prefix it with `experiment <name>: `.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from crab.core.allocation.allocator import NodeAllocator
from crab.core.allocation.chains import resolve_chains
from crab.core.execution.settings import ExecutionSettings, check_dropped_keys

AUTO_SPLIT_WARNING = (
    "automatic disjoint core split ignores NUMA and NIC locality; set cpus per app to control it"
)
SHARED_CORES_WARNING = "cores: shared measures OS scheduling as much as the hardware"


@dataclass(frozen=True)
class Head:
    """A chain head: one app that gets its own share of the nodes.

    `key` names it in messages ("app 0"), `cpus` is its parsed core list or None, `shared` is true
    when its allocation scope says `cores: "shared"`.
    """

    key: str
    nodes: frozenset[str]
    cpus: tuple[int, ...] | None
    shared: bool


@dataclass(frozen=True)
class Placement:
    """The result for one experiment, one entry per app in app order."""

    cpu_lists: list[tuple[int, ...] | None]
    envs: list[dict[str, str]]
    warnings: list[str]


def parse_cpus(text: object, where: str) -> tuple[int, ...]:
    """Parse "0-3,8" into the sorted cores (0, 1, 2, 3, 8).

    Raises:
        ValueError: `text` is not a string of comma-separated `N` or `A-B` (A <= B) items, or
            a core appears twice.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}: cpus must be a core list such as '0-3,8', got {text!r}")
    cores: list[int] = []
    for item in text.split(","):
        item = item.strip()
        single = re.fullmatch(r"[0-9]+", item)
        span = re.fullmatch(r"([0-9]+)-([0-9]+)", item)
        if single:
            cores.append(int(item))
        elif span and int(span[1]) <= int(span[2]):
            cores.extend(range(int(span[1]), int(span[2]) + 1))
        else:
            raise ValueError(
                f"{where}: cpus {text!r} has the invalid item {item!r}; "
                "use N or A-B (A <= B), comma-separated"
            )
    if len(set(cores)) != len(cores):
        raise ValueError(f"{where}: cpus {text!r} lists a core more than once")
    return tuple(sorted(cores))


def parse_app_env(value: object, where: str) -> dict[str, str]:
    """An app's `env`: an object of strings, without the dropped `CRAB_*` launch keys.

    Raises:
        ValueError: `value` is not an object of strings, or holds a dropped key.
    """
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(f"{where}: must be an object of strings, got {value!r}")
    check_dropped_keys(value, where)
    return dict(value)


def has_exclusive(sbatch_directives: Sequence[Any]) -> bool:
    """True when `--exclusive` is among the directives (list or dict form of each source)."""
    for source in sbatch_directives:
        if isinstance(source, dict):
            if any(str(k).lstrip("-") == "exclusive" and v is not False for k, v in source.items()):
                return True
        elif isinstance(source, list):
            for raw in source:
                words = str(raw).strip().lstrip("-").split()
                if words and words[0].split("=")[0] == "exclusive":
                    return True
    return False


def _groups(heads: Sequence[Head]) -> list[list[Head]]:
    """Heads whose node sets intersect, directly or through another head, in input order."""
    groups: list[list[Head]] = []
    for head in heads:
        touching = [g for g in groups if any(head.nodes & other.nodes for other in g)]
        merged = [head]
        for group in touching:
            groups.remove(group)
            merged = group + merged
        groups.append(merged)
    return groups


def _cores_text(cores: Sequence[int]) -> str:
    return ",".join(str(c) for c in sorted(cores))


def _check_overlap(group: Sequence[Head]) -> None:
    explicit = [h for h in group if h.cpus]
    for i, first in enumerate(explicit):
        for second in explicit[i + 1 :]:
            common = set(first.cpus or ()) & set(second.cpus or ())
            if common:
                raise ValueError(
                    f"{first.key} and {second.key} run at the same time on the same nodes and "
                    f"both use core(s) {_cores_text(sorted(common))}; give each app its own cpus "
                    'or set cores to "shared"'
                )


def _split_automatically(
    group: Sequence[Head], ppn: int, host_cores: int | None, result: dict[str, Any]
) -> bool:
    """Give the group's heads without `cpus` contiguous blocks of `ppn` free cores.

    Returns True when a block was handed out.
    """
    auto = [h for h in group if h.cpus is None]
    if not auto:
        return False
    taken = {c for h in group for c in (h.cpus or ())}
    needed = ppn * len(auto)
    pool = range(host_cores if host_cores is not None else max(taken, default=-1) + 1 + needed)
    free = [c for c in pool if c not in taken]
    if len(free) < needed:
        raise ValueError(
            f"{len(auto)} apps without cpus need {needed} cores ({len(auto)} x ppn {ppn}) on one "
            f"host, but only {len(free)} of its {host_cores} cores are free; lower ppn, "
            "use fewer apps or set cpus"
        )
    for index, head in enumerate(auto):
        result[head.key] = tuple(free[index * ppn : (index + 1) * ppn])
    return True


def plan(
    heads: Sequence[Head],
    ppn: int,
    scheduler: str,
    host_cores: int | None,
    sbatch_directives: Sequence[Any],
) -> tuple[dict[str, tuple[int, ...] | None], list[str]]:
    """Decide each chain head's core list (None means the launcher's default) and the warnings.

    Args:
        heads: the experiment's chain heads.
        ppn: ranks per node.
        scheduler: "slurm" or "local".
        host_cores: the fewest cores of the hosts used, or None when unknown (Slurm).
        sbatch_directives: the raw `sbatch_directives` and `system_sbatch` values.

    Raises:
        ValueError: `cpus` shorter than `ppn`, `cpus` under Slurm without `--exclusive`,
            overlapping `cpus` of apps running together, or more cores wanted than the host has.
    """
    for head in heads:
        if head.cpus is not None and len(head.cpus) < ppn:
            raise ValueError(
                f"{head.key}: cpus lists {len(head.cpus)} core(s), fewer than ppn {ppn} "
                "(one core per rank)"
            )
    if (
        scheduler == "slurm"
        and any(h.cpus is not None for h in heads)
        and not has_exclusive(sbatch_directives)
    ):
        raise ValueError(
            "cpus under Slurm needs --exclusive among the sbatch directives "
            "(without it the cores are not yours alone)"
        )
    result: dict[str, tuple[int, ...] | None] = {h.key: h.cpus for h in heads}
    warnings: list[str] = []
    for group in _groups(heads):
        if len(group) < 2:
            continue
        if any(h.shared for h in group):
            if SHARED_CORES_WARNING not in warnings:
                warnings.append(SHARED_CORES_WARNING)
            continue
        _check_overlap(group)
        # Under Slurm the apps stay unplaced here: srun --exact separates them.
        if scheduler == "local" and _split_automatically(group, ppn, host_cores, result):
            if AUTO_SPLIT_WARNING not in warnings:
                warnings.append(AUTO_SPLIT_WARNING)
    return result, warnings


def host_cores_of(settings: ExecutionSettings, node_list: Sequence[str]) -> int | None:
    """The fewest cores of the hosts in `node_list`, or None under Slurm or when unknown.

    A host without a count (a bare `localhost`) or a name the preset does not list counts as this
    machine's `os.cpu_count()`.
    """
    if settings.scheduler != "local":
        return None
    by_name = {host.name: host.cores for host in settings.hosts}
    here = os.cpu_count()
    counts = [by_name.get(name) or here for name in node_list]
    known = [c for c in counts if c]
    return min(known) if known else None


def _shared_cores(allocation: Mapping[str, Any], partition: str | None) -> bool:
    """Whether the allocation scope an app lives in says `cores: "shared"`."""
    partitions = allocation.get("partitions")
    scope: Mapping[str, Any] = allocation
    if partitions:
        member = partitions.get(partition) if isinstance(partitions, dict) else None
        scope = member if isinstance(member, dict) else {}
    return scope.get("mode") == "shared" and scope.get("cores") == "shared"


def place_experiment(
    exp: str,
    keys: Sequence[str],
    configs: Sequence[Mapping[str, Any]],
    nodes: Sequence[Sequence[str]],
    allocation: Mapping[str, Any],
    ppn: int,
    settings: ExecutionSettings,
    node_list: Sequence[str],
    global_opts: Mapping[str, Any],
) -> Placement:
    """Place one experiment's apps, given each app's config key, config and allocated nodes.

    Raises:
        ValueError: an invalid `cpus` or `env`, a `cpus` on a chained app, or a failure of `plan`;
            every message starts with `experiment <exp>: `.
    """
    try:
        return _place(keys, configs, nodes, allocation, ppn, settings, node_list, global_opts)
    except ValueError as error:
        raise ValueError(f"experiment {exp}: {error}") from error


def _place(
    keys: Sequence[str],
    configs: Sequence[Mapping[str, Any]],
    nodes: Sequence[Sequence[str]],
    allocation: Mapping[str, Any],
    ppn: int,
    settings: ExecutionSettings,
    node_list: Sequence[str],
    global_opts: Mapping[str, Any],
) -> Placement:
    starts = [str(cfg.get("start", "0")) for cfg in configs]
    partitions = [cfg.get("partition") or None for cfg in configs]
    reuse = resolve_chains(starts, partitions)
    envs = [
        parse_app_env(cfg.get("env", {}), f"app {key}: env")
        for key, cfg in zip(keys, configs, strict=True)
    ]
    heads: list[Head] = []
    for i, (key, cfg) in enumerate(zip(keys, configs, strict=True)):
        if i in reuse:
            if "cpus" in cfg:
                raise ValueError(
                    f"app {key}: cpus cannot be set on an app that starts after another "
                    f"(start {starts[i]!r}); it runs on app {keys[reuse[i]]}'s cores, "
                    "so set cpus there"
                )
            continue
        cpus = parse_cpus(cfg["cpus"], f"app {key}") if "cpus" in cfg else None
        heads.append(
            Head(f"app {key}", frozenset(nodes[i]), cpus, _shared_cores(allocation, partitions[i]))
        )
    directives = [global_opts.get("sbatch_directives"), global_opts.get("system_sbatch")]
    result, warnings = plan(
        heads, ppn, settings.scheduler, host_cores_of(settings, node_list), directives
    )
    cpu_lists = [result[f"app {keys[reuse.get(i, i)]}"] for i in range(len(keys))]
    return Placement(cpu_lists, envs, warnings)


class _Slot:
    """Stands in for an app so the real allocator can be run on paper."""

    def __init__(self, partition_id: Any, start: str) -> None:
        self.partition_id = partition_id
        self.start_string = start
        self.nodes: list[str] = []

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)


def allocate_on_paper(
    allocation: dict[str, Any], partitions: list[Any], starts: list[str], node_list: list[str]
) -> list[list[str]]:
    """The nodes of each app, from the same NodeAllocator call as ExperimentRunner.setup."""
    slots = [_Slot(p, s) for p, s in zip(partitions, starts, strict=True)]
    NodeAllocator.allocate_experiment(slots, node_list, allocation)
    return [s.nodes for s in slots]


def check_experiment(
    exp: str,
    apps: Mapping[str, Mapping[str, Any]],
    allocation: dict[str, Any],
    global_opts: Mapping[str, Any],
    settings: ExecutionSettings,
) -> list[str]:
    """Run the placement of one experiment on paper; return its warnings or raise.

    `apps` is the experiment's `apps` object, `allocation` its allocation as the experiment sees
    it. Skipped (returns no warnings) when `numnodes` or `ppn` is missing or not a number, as the
    allocation checks skip it.

    Raises:
        ValueError: as `place_experiment`.
    """
    try:
        numnodes = int(global_opts["numnodes"])
        ppn = int(global_opts.get("ppn", 1))
    except (KeyError, TypeError, ValueError):
        return []
    keys = sorted(apps.keys(), key=lambda x: int(x) if x.isdigit() else x)  # runner.py order
    configs = [apps[k] for k in keys]
    if settings.scheduler == "local":
        node_list = [host.name for host in settings.hosts][:numnodes]
    else:
        node_list = [f"n{i}" for i in range(numnodes)]
    app_nodes = allocate_on_paper(
        allocation,
        [cfg.get("partition") or None for cfg in configs],
        [str(cfg.get("start", "0")) for cfg in configs],
        node_list,
    )
    placed = place_experiment(
        exp, keys, configs, app_nodes, allocation, ppn, settings, node_list, global_opts
    )
    return placed.warnings
