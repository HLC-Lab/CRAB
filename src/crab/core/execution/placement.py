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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from crab.core.allocation.chains import resolve_chains
from crab.core.execution.bind_apply import AppBind, apply_binds
from crab.core.execution.placement_parse import (
    app_ppn,
    has_exclusive,
    parse_app_env,
    parse_app_gpus,
    parse_app_nics,
    parse_cpus,
)
from crab.core.execution.placement_parse import parse_ppn as parse_ppn
from crab.core.execution.settings import ExecutionSettings
from crab.core.execution.topology import device_env

AUTO_SPLIT_WARNING = (
    "automatic disjoint core split ignores NUMA and NIC locality; set cpus per app to control it"
)
SHARED_CORES_WARNING = "cores: shared measures OS scheduling as much as the hardware"


CoRun = Literal["disjoint", "shared"]


@dataclass(frozen=True)
class Head:
    """A chain head: one app that gets its own share of the nodes.

    `key` names it in messages ("app 0"), `cpus` is its parsed core list or None, `shared` is true
    when its allocation scope says `cores: "shared"`, `ppn` is its ranks per node.
    """

    key: str
    nodes: frozenset[str]
    cpus: tuple[int, ...] | None
    shared: bool
    ppn: int


@dataclass(frozen=True)
class Placement:
    """The result for one experiment, one entry per app in app order.

    `shared_cores[i]` is true when app i runs at the same time as another app on the same nodes
    under `cores: "shared"` (it competes for cores and must not be bound by the launcher).
    `co_run[i]` is None for an app alone on its nodes, else "shared" or "disjoint" by the cores
    setting of the group of apps it runs with (an app after another takes its head's value).
    `nics[i]` is app i's own `nics` list, or None when it sets none (a chained app keeps its own).
    `gpus[i]` is app i's own `gpus` list, or the GPUs its `bind` chose, or None when neither sets any.
    `binds[i]` is what app i's `bind` settled (an `AppBind`), or None when it has no `bind`.
    """

    cpu_lists: list[tuple[int, ...] | None]
    envs: list[dict[str, str]]
    warnings: list[str]
    shared_cores: list[bool] = field(default_factory=list)
    co_run: list[CoRun | None] = field(default_factory=list)
    nics: list[tuple[str, ...] | None] = field(default_factory=list)
    gpus: list[tuple[int, ...] | None] = field(default_factory=list)
    binds: list[AppBind | None] = field(default_factory=list)


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


def co_run_modes(heads: Sequence[Head]) -> dict[str, CoRun]:
    """For each head that overlaps another head's nodes, "shared" when its group says
    `cores: "shared"`, else "disjoint". Heads alone on their nodes are left out."""
    modes: dict[str, CoRun] = {}
    for group in _groups(heads):
        if len(group) >= 2:
            mode: CoRun = "shared" if any(h.shared for h in group) else "disjoint"
            modes.update((h.key, mode) for h in group)
    return modes


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


def _check_cpus_on_all_or_none(group: Sequence[Head]) -> None:
    """Under Slurm, the apps of a group that set `cpus` and the apps that do not cannot mix.

    An `--overlap --cpu-bind=map_cpu` step does not reserve its cores, so a concurrent `--exact`
    step may be given them, and mpirun inside Slurm binds an app without cpus by its default
    (the first cores).
    """
    if all(h.cpus is None for h in group) or all(h.cpus is not None for h in group):
        return
    names = [h.key for h in group if h.cpus is None]
    many = len(names) > 1
    raise ValueError(
        f"{' and '.join(names)} {'share' if many else 'shares'} nodes with apps that set cpus "
        f"but set none {'themselves' if many else 'itself'}; under Slurm set cpus on every app "
        "of the group, or on none (an app without cpus may be given the cores of one that has "
        "them)"
    )


def _split_automatically(
    group: Sequence[Head], host_cores: int | None, result: dict[str, Any]
) -> bool:
    """Give the group's heads without `cpus` contiguous blocks of free cores, `ppn` each.

    Returns True when a block was handed out.
    """
    auto = [h for h in group if h.cpus is None]
    if not auto:
        return False
    taken = {c for h in group for c in (h.cpus or ())}
    needed = sum(h.ppn for h in auto)
    pool = range(host_cores if host_cores is not None else max(taken, default=-1) + 1 + needed)
    free = [c for c in pool if c not in taken]
    if len(free) < needed:
        raise ValueError(
            f"{len(auto)} apps without cpus need {needed} cores (the sum of their ppn) on one "
            f"host, but only {len(free)} of its {host_cores} cores are free; lower ppn, "
            "use fewer apps or set cpus"
        )
    start = 0
    for head in auto:
        result[head.key] = tuple(free[start : start + head.ppn])
        start += head.ppn
    return True


def plan(
    heads: Sequence[Head],
    scheduler: str,
    host_cores: int | None,
    sbatch_directives: Sequence[Any],
) -> tuple[dict[str, tuple[int, ...] | None], list[str]]:
    """Decide each chain head's core list (None means the launcher's default) and the warnings.

    Args:
        heads: the experiment's chain heads.
        scheduler: "slurm" or "local".
        host_cores: the fewest cores of the hosts used, or None when unknown (Slurm).
        sbatch_directives: the raw `sbatch_directives` and `system_sbatch` values.

    Raises:
        ValueError: `cpus` shorter than the head's `ppn`, `cpus` under Slurm without `--exclusive`,
            overlapping `cpus` of apps running together, under Slurm `cpus` on some apps of a
            group sharing nodes and not on others, or more cores wanted than the host has.
    """
    for head in heads:
        if head.cpus is not None and len(head.cpus) < head.ppn:
            raise ValueError(
                f"{head.key}: cpus lists {len(head.cpus)} core(s), fewer than ppn {head.ppn} "
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
        if scheduler == "slurm":
            _check_cpus_on_all_or_none(group)
        # Under Slurm no core list is chosen here: the srun launcher binds each co-run app to its
        # own cores (`--exact --cpu-bind=cores`), or to its `cpus`.
        if scheduler == "local" and _split_automatically(group, host_cores, result):
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
    exp_opts: Mapping[str, Any],
    settings: ExecutionSettings,
    node_list: Sequence[str],
    global_opts: Mapping[str, Any],
) -> Placement:
    """Place one experiment's apps, given each app's config key, config and allocated nodes.

    `exp_opts` is the experiment's merged options, where each app's ranks per node come from.

    Raises:
        ValueError: an invalid `cpus`, `env`, `nics`, `gpus`, `bind` or `ppn`, `nics` together with
            `env.UCX_NET_DEVICES`, `gpus` together with the GPU variable of the env, a `bind` that
            sets what a literal key or env variable also sets, a `bind.gpu` together with
            `--gpus-per-task` or `--gpu-bind` in the sbatch directives, a `bind` without a topology
            in the preset or one the topology cannot meet, a `cpus` or `bind.cores` on a chained
            app, a chained app with more ranks than its head's cores, or a failure of `plan`;
            every message starts with `experiment <exp>: `.
    """
    try:
        return _place(keys, configs, nodes, allocation, exp_opts, settings, node_list, global_opts)
    except ValueError as error:
        raise ValueError(f"experiment {exp}: {error}") from error


def _place(
    keys: Sequence[str],
    configs: Sequence[Mapping[str, Any]],
    nodes: Sequence[Sequence[str]],
    allocation: Mapping[str, Any],
    exp_opts: Mapping[str, Any],
    settings: ExecutionSettings,
    node_list: Sequence[str],
    global_opts: Mapping[str, Any],
) -> Placement:
    starts = [str(cfg.get("start", "0")) for cfg in configs]
    partitions = [cfg.get("partition") or None for cfg in configs]
    reuse = resolve_chains(starts, partitions)
    ppns = []
    for key, cfg in zip(keys, configs, strict=True):
        try:
            ppns.append(app_ppn(cfg, exp_opts))
        except ValueError as error:
            raise ValueError(f"app {key}: {error}") from error
    envs = [
        parse_app_env(cfg.get("env", {}), f"app {key}: env")
        for key, cfg in zip(keys, configs, strict=True)
    ]
    nic_var, gpu_var = device_env(settings.topology)
    nics: list[tuple[str, ...] | None] = []
    gpus: list[tuple[int, ...] | None] = []
    for key, cfg, env in zip(keys, configs, envs, strict=True):
        app_nics = parse_app_nics(cfg["nics"], f"app {key}: nics") if "nics" in cfg else None
        if app_nics is not None and nic_var in env:
            raise ValueError(f"app {key}: nics and env {nic_var} both set the NIC; keep one")
        nics.append(app_nics)
        app_gpus = parse_app_gpus(cfg["gpus"], f"app {key}: gpus") if "gpus" in cfg else None
        if app_gpus is not None and gpu_var in env:
            raise ValueError(f"app {key}: gpus and env {gpu_var} both set the GPU; keep one")
        gpus.append(app_gpus)
    literal = [
        parse_cpus(cfg["cpus"], f"app {key}") if "cpus" in cfg and i not in reuse else None
        for i, (key, cfg) in enumerate(zip(keys, configs, strict=True))
    ]
    directives = [global_opts.get("sbatch_directives"), global_opts.get("system_sbatch")]
    bound = apply_binds(keys, configs, envs, ppns, nodes, set(reuse), literal, settings, directives)
    for i, applied in enumerate(bound):
        if applied is not None:
            nics[i] = applied.nics if applied.nics is not None else nics[i]
            gpus[i] = applied.gpus if applied.gpus is not None else gpus[i]
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
        applied = bound[i]
        cpus = applied.cpus if applied is not None and applied.cpus is not None else literal[i]
        heads.append(
            Head(
                f"app {key}",
                frozenset(nodes[i]),
                cpus,
                _shared_cores(allocation, partitions[i]),
                ppns[i],
            )
        )
    result, warnings = plan(
        heads, settings.scheduler, host_cores_of(settings, node_list), directives
    )
    warnings += [w for applied in bound if applied is not None for w in applied.warnings]
    for i, head_index in reuse.items():
        cores = result[f"app {keys[head_index]}"]
        if cores is not None and ppns[i] > len(cores):
            raise ValueError(
                f"app {keys[i]}: ppn {ppns[i]} is more than app {keys[head_index]}'s "
                f"{len(cores)} core(s), the cores it runs on"
            )
    cpu_lists = [result[f"app {keys[reuse.get(i, i)]}"] for i in range(len(keys))]
    modes = co_run_modes(heads)
    co_run = [modes.get(f"app {keys[reuse.get(i, i)]}") for i in range(len(keys))]
    shared_cores = [mode == "shared" for mode in co_run]
    return Placement(cpu_lists, envs, warnings, shared_cores, co_run, nics, gpus, bound)
