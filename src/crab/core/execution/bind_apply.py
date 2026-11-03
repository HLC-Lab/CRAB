"""Applies each app's `bind` while an experiment is placed (ADR-043).

`place_experiment` calls `apply_binds` for every experiment, before submit (on paper) and again when
the worker runs it, so both see the same answer from the preset's `topology`. What comes back is
what the literal keys would have said: one core per rank in rank order (the resolver's order is kept,
unlike a literal `cpus`, which is sorted) and one NIC per rank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from crab.core.execution.bind import Bind, parse_bind
from crab.core.execution.bind_resolve import RankPlacement, resolve
from crab.core.execution.placement_parse import has_sbatch_option
from crab.core.execution.settings import ExecutionSettings
from crab.core.execution.topology import device_env


@dataclass(frozen=True)
class AppBind:
    """What one app's `bind` settled. None means the launcher's default for that part."""

    cpus: tuple[int, ...] | None
    nics: tuple[str, ...] | None
    gpus: tuple[int, ...] | None
    warnings: tuple[str, ...]
    ranks: tuple[RankPlacement, ...] = ()


class _Held(NamedTuple):
    """What an earlier app holds: its chain's group (the head's index), its nodes, cores and GPUs."""

    group: int
    nodes: frozenset[str]
    cores: frozenset[int]
    gpus: frozenset[int]


def _refuse_conflicts(
    bind: Bind,
    config: Mapping[str, Any],
    env: Mapping[str, str],
    where: str,
    settings: ExecutionSettings,
) -> None:
    nic_var, gpu_var = device_env(settings.topology)
    if bind.cores == "nearest" and "cpus" in config:
        raise ValueError(f"{where}: bind.cores and cpus both place the cores; keep one")
    if bind.nic.pick != "default" and ("nics" in config or nic_var in env):
        raise ValueError(
            f"{where}: bind.nic and nics (or env {nic_var}) both set the NIC; keep one"
        )
    if bind.gpu in ("per-rank", "shared") and ("gpus" in config or gpu_var in env):
        raise ValueError(
            f"{where}: bind.gpu and gpus (or env {gpu_var}) both set the GPU; keep one"
        )


def _refuse_slurm_gpu_binding(bind: Bind, directives: Sequence[Any], where: str) -> None:
    """Slurm renumbers the GPUs it binds, so GPU indexes from the topology would point at the wrong ones."""
    if bind.gpu in ("per-rank", "shared") and has_sbatch_option(
        directives, {"gpus-per-task", "gpu-bind"}
    ):
        raise ValueError(
            f'{where}: bind.gpu "{bind.gpu}" sets the GPUs itself, so --gpus-per-task and --gpu-bind '
            "cannot be among the sbatch directives (Slurm would renumber them)"
        )


def apply_binds(
    keys: Sequence[str],
    configs: Sequence[Mapping[str, Any]],
    envs: Sequence[Mapping[str, str]],
    ppns: Sequence[int],
    nodes: Sequence[Sequence[str]],
    chain_head: Mapping[int, int],
    literal_cpus: Sequence[tuple[int, ...] | None],
    literal_gpus: Sequence[tuple[int, ...] | None],
    settings: ExecutionSettings,
    directives: Sequence[Any] = (),
) -> list[AppBind | None]:
    """One entry per app: None when it has no `bind` (or one that asks for nothing).

    `chain_head` maps each app's index to the index of the head of its chain: an app that starts
    after another runs on the nodes of its head and shares that chain's group, and a head maps to
    itself. An app is placed after the cores and the GPUs held by apps of other groups on the same
    nodes. `literal_cpus` and `literal_gpus` are each app's parsed `cpus` and `gpus`, which the app
    holds when it has no `bind` on that axis; an app's `bind.gpu: per-rank` GPUs are held too, while
    `shared` and `all` hold none. `directives` are the raw sbatch directive sources.

    Raises:
        ValueError: an invalid `bind`, a `bind` and a literal key on the same axis, a `bind` on an
            app without a topology in the preset, or a request the topology cannot meet.
    """
    applied: list[AppBind | None] = []
    held: list[_Held] = []
    for i, key in enumerate(keys):
        where = f"app {key}"
        group = chain_head[i]
        chained = group != i
        cores: frozenset[int] = frozenset(literal_cpus[i] or ())
        gpus_held: frozenset[int] = frozenset(literal_gpus[i] or ())
        result: AppBind | None = None
        if "bind" in configs[i]:
            bind = parse_bind(configs[i]["bind"], where)
            if bind != Bind():
                _refuse_conflicts(bind, configs[i], envs[i], where, settings)
                _refuse_slurm_gpu_binding(bind, directives, where)
                if chained and bind.cores == "nearest":
                    raise ValueError(
                        f"{where}: bind.cores cannot be set on an app that starts after another; "
                        "it runs on the cores of the app before it"
                    )
                if settings.topology is None:
                    raise ValueError(
                        f"{where}: bind needs a topology in the preset, and this preset has none "
                        "(crab probe prints one for a node)"
                    )
                taken = frozenset().union(
                    *(h.cores for h in held if h.group != group and h.nodes & set(nodes[i]))
                )
                taken_gpus = frozenset().union(
                    *(h.gpus for h in held if h.group != group and h.nodes & set(nodes[i]))
                )
                placed = resolve(
                    bind, settings.topology, ppns[i], taken, where, taken_gpus=taken_gpus
                )
                cpus = tuple(r.cores[0] for r in placed.ranks) if bind.cores == "nearest" else None
                nics = (
                    tuple(",".join(r.nics) for r in placed.ranks)
                    if bind.nic.pick != "default"
                    else None
                )
                gpus = (
                    tuple(int(r.gpu) for r in placed.ranks if r.gpu is not None)
                    if bind.gpu in ("per-rank", "shared")
                    else None
                )
                if bind.gpu == "per-rank" and gpus is not None:
                    gpus_held = frozenset(gpus)
                result = AppBind(cpus, nics, gpus, placed.warnings, placed.ranks)
                cores = placed.cores_used or cores
        applied.append(result)
        held.append(_Held(group, frozenset(nodes[i]), cores, gpus_held))
    return applied
