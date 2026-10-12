"""Placement of one experiment on paper, before anything runs (ADR-033).

Runs the real NodeAllocator and `place_experiment` without a job, so the pre-submit check can
refuse a config the run would refuse. Every error is a `ValueError`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from crab.core.allocation.allocator import NodeAllocator
from crab.core.execution.placement import app_ppn, place_experiment
from crab.core.execution.settings import ExecutionSettings, resolve_launcher


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
    launcher: str | None = None,
    local_options: Mapping[str, Any] | None = None,
) -> list[str]:
    """Run the placement of one experiment on paper; return its warnings or raise.

    `apps` is the experiment's `apps` object, `allocation` its allocation as the experiment sees
    it, `launcher` the experiment's own `launcher` option (the preset's when None; a receipt's
    override is not known before submit), `local_options` its own options, which override
    `global_opts` for `ppn`. An explicit `cpus` under the direct launcher, which does not bind to
    it, is warned about once per app; under srun, a `--cpu-bind` flag that CRAB's binding replaces
    (a co-run app or a `cpus`) is warned about once. Skipped (returns no warnings) when `numnodes` or an
    app's `ppn` is missing or not a number, as the allocation checks skip it.

    Raises:
        ValueError: as `place_experiment`.
    """
    exp_opts = {**global_opts, **(local_options or {})}
    keys = sorted(apps.keys(), key=lambda x: int(x) if x.isdigit() else x)  # runner.py order
    configs = [apps[k] for k in keys]
    try:
        numnodes = int(global_opts["numnodes"])
        for cfg in configs:
            app_ppn(cfg, exp_opts)
    except (KeyError, TypeError, ValueError):
        return []
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
        exp, keys, configs, app_nodes, allocation, exp_opts, settings, node_list, global_opts
    )
    effective = launcher or settings.launcher
    ignored = [
        f"experiment {exp}: app {key}: cpus has no effect with the {effective} launcher; "
        "only mpirun binds to an explicit core list"
        for key, cfg in zip(keys, configs, strict=True)
        if "cpus" in cfg and effective == "direct"
    ]
    binds = any(placed.co_run) or any("cpus" in cfg for cfg in configs)
    return (
        placed.warnings
        + ignored
        + (_cpu_bind_note(exp, exp_opts, settings, launcher) if binds else [])
    )


def _cpu_bind_note(
    exp: str, exp_opts: Mapping[str, Any], settings: ExecutionSettings, launcher: str | None
) -> list[str]:
    """The warning that CRAB's core binding replaces a `--cpu-bind` flag of the srun launcher."""
    try:
        spec = resolve_launcher(
            settings, {**exp_opts, **({"launcher": launcher} if launcher else {})}, None
        )
    except ValueError:
        return []  # an invalid launcher is refused by the launcher checks
    for flag in spec.flags if spec.kind == "srun" else ():
        if flag.split("=")[0] in ("--cpu-bind", "--cpu_bind"):
            return [
                f"experiment {exp}: launcher flag '{flag}' is replaced by CRAB's core binding "
                "for apps that share nodes or set cpus"
            ]
    return []
