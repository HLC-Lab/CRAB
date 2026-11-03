"""Applies each app's `bind` while an experiment is placed (ADR-043).

`place_experiment` calls `apply_binds` for every experiment, before submit (on paper) and again when
the worker runs it, so both see the same answer from the preset's `topology`. What comes back is
what the literal keys would have said: one core per rank in rank order (the resolver's order is kept,
unlike a literal `cpus`, which is sorted) and one NIC per rank.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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


def _gpu_overlap_warnings(
    where: str,
    nodes: frozenset[str],
    gpus: frozenset[int],
    held_gpus: Sequence[tuple[str, frozenset[str], frozenset[int]]],
) -> tuple[str, ...]:
    """One sentence per earlier app on a shared node that holds some of the GPUs this app was given."""
    sentences = []
    for other, other_nodes, other_gpus in held_gpus:
        common = gpus & other_gpus
        if common and nodes & other_nodes:
            listed = ", ".join(str(g) for g in sorted(common))
            sentences.append(f"{where}: GPU {listed} of the node are also bound by app {other}")
    return tuple(sentences)


def apply_binds(
    keys: Sequence[str],
    configs: Sequence[Mapping[str, Any]],
    envs: Sequence[Mapping[str, str]],
    ppns: Sequence[int],
    nodes: Sequence[Sequence[str]],
    chained: Collection[int],
    literal_cpus: Sequence[tuple[int, ...] | None],
    settings: ExecutionSettings,
    directives: Sequence[Any] = (),
) -> list[AppBind | None]:
    """One entry per app: None when it has no `bind` (or one that asks for nothing).

    `chained` holds the indexes of apps that start after another and run on its cores;
    `literal_cpus` is each app's parsed `cpus`, so apps are placed after the cores earlier apps
    on the same nodes already hold; `directives` are the raw sbatch directive sources.

    Raises:
        ValueError: an invalid `bind`, a `bind` and a literal key on the same axis, a `bind` on an
            app without a topology in the preset, or a request the topology cannot meet.
    """
    applied: list[AppBind | None] = []
    held: list[tuple[frozenset[str], frozenset[int]]] = []
    held_gpus: list[tuple[str, frozenset[str], frozenset[int]]] = []
    for i, key in enumerate(keys):
        where = f"app {key}"
        cores: frozenset[int] = frozenset(literal_cpus[i] or ())
        result: AppBind | None = None
        per_rank_gpus: frozenset[int] = frozenset()
        if "bind" in configs[i]:
            bind = parse_bind(configs[i]["bind"], where)
            if bind != Bind():
                _refuse_conflicts(bind, configs[i], envs[i], where, settings)
                _refuse_slurm_gpu_binding(bind, directives, where)
                if i in chained and bind.cores == "nearest":
                    raise ValueError(
                        f"{where}: bind.cores cannot be set on an app that starts after another; "
                        "it runs on the cores of the app before it"
                    )
                if settings.topology is None:
                    raise ValueError(
                        f"{where}: bind needs a topology in the preset, and this preset has none "
                        "(crab probe prints one for a node)"
                    )
                taken = frozenset().union(*(c for n, c in held if n & set(nodes[i])))
                placed = resolve(bind, settings.topology, ppns[i], taken, where)
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
                warnings = placed.warnings
                if bind.gpu == "per-rank" and gpus is not None:
                    per_rank_gpus = frozenset(gpus)
                    if i not in chained:  # it starts after another app, so it never runs beside it
                        warnings += _gpu_overlap_warnings(
                            where, frozenset(nodes[i]), per_rank_gpus, held_gpus
                        )
                result = AppBind(cpus, nics, gpus, warnings, placed.ranks)
                cores = placed.cores_used or cores
        applied.append(result)
        if i not in chained:
            held.append((frozenset(nodes[i]), cores))
            if per_rank_gpus:
                held_gpus.append((key, frozenset(nodes[i]), per_rank_gpus))
    return applied
