"""The batch job's `--ntasks-per-node`, computed from the config (ADR-036).

Apps set their own `ppn`, and apps in `allocation.mode: "shared"` run on the same nodes at the
same time, so one global `ppn` no longer sizes the job. The job needs the largest per-node load of
any experiment: on a node, chain heads that hold it add up, and an app that starts after another
one on its nodes (ADR-032) counts once per chain, at the chain's largest `ppn`. Experiments run
one after the other, so the job takes the maximum over them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from crab.core.allocation.chains import resolve_chains
from crab.core.execution.placement import app_ppn
from crab.core.execution.placement_checks import allocate_on_paper


def _experiment_load(experiment: Mapping[str, Any], global_opts: Mapping[str, Any]) -> int:
    """The largest per-node load of one experiment (0 when it has no app).

    Raises:
        ValueError: a `ppn` that applies or `numnodes` is not a positive integer, or the
            allocation or the starts are invalid.
        KeyError: `numnodes` is missing.
        TypeError: `numnodes` is not a number.
    """
    exp_opts = {**global_opts, **experiment.get("local_options", {})}
    apps = experiment.get("apps", {})
    keys = sorted(apps, key=lambda x: int(x) if x.isdigit() else x)  # runner.py order
    configs = [apps[k] for k in keys]
    if not configs:
        return 0
    ppns = [app_ppn(cfg, exp_opts) for cfg in configs]
    node_list = [f"n{i}" for i in range(int(global_opts["numnodes"]))]
    partitions = [cfg.get("partition") or None for cfg in configs]
    starts = [str(cfg.get("start", "0")) for cfg in configs]
    app_nodes = allocate_on_paper(exp_opts.get("allocation", {}), partitions, starts, node_list)
    reuse = resolve_chains(starts, partitions)
    chain_ppn = {i: ppns[i] for i in range(len(configs)) if i not in reuse}
    for app, head in reuse.items():
        chain_ppn[head] = max(chain_ppn[head], ppns[app])
    load: dict[str, int] = {}
    for head, ppn in chain_ppn.items():
        for node in app_nodes[head]:
            load[node] = load.get(node, 0) + ppn
    return max(load.values(), default=0)


def job_tasks_per_node(config: Mapping[str, Any]) -> int | None:
    """The largest per-node load over all experiments, or None when it cannot be computed.

    None (no experiment with an app, `numnodes` missing or not a number, a `{var}` token or any
    other invalid value where it is needed) tells the caller to keep the global `ppn`.
    """
    global_opts = config.get("global_options", {})
    try:
        loads = [
            _experiment_load(exp, global_opts) for exp in config.get("experiments", {}).values()
        ]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return max(loads, default=0) or None
