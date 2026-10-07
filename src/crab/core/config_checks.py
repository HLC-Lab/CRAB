"""Checks that turn silently ignored config values into errors before anything runs.

Only rules the engine would otherwise get wrong live here. Everything else stays permissive,
because configs are hand-edited and unknown app keys are wrapper attributes.
"""

from __future__ import annotations

from typing import Any

from crab.core.allocation.allocator import NodeAllocator

ALLOCATION_MODES = ("linear", "interleaved", "random")
OUTPUT_FORMATS = ("csv",)
_BOOL_OPTIONS = ("convergeall", "retain_files")


def parse_bool(value: Any, name: str) -> bool:
    """A config boolean: JSON true/false, or the strings "true"/"false" in any case.

    Strings are accepted because SbatchMan substitutes swept values as text.

    Raises:
        ValueError: for anything else (e.g. "yes", 1, "").
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _experiments(config: dict[str, Any]) -> dict[str, Any]:
    """The experiments to check, including the legacy top-level `applications` form."""
    if "experiments" in config:
        return config["experiments"] or {}
    legacy = config.get("applications")
    if isinstance(legacy, dict):
        return {"default_ex": legacy if "apps" in legacy else {"apps": legacy}}
    return {}


def _check_mode(mode: Any, where: str) -> None:
    if mode not in ALLOCATION_MODES:
        raise ValueError(f"{where} {mode!r} is not one of: {', '.join(ALLOCATION_MODES)}")


def _check_options(opts: dict[str, Any], exp: str) -> None:
    for key in _BOOL_OPTIONS:
        if key in opts:
            parse_bool(opts[key], f"experiment {exp}: {key}")

    if "outformat" in opts and opts["outformat"] not in OUTPUT_FORMATS:
        raise ValueError(
            f"experiment {exp}: outformat {opts['outformat']!r} is not supported, use csv"
        )

    allocation = opts.get("allocation") or {}
    if "mode" in allocation:
        _check_mode(allocation["mode"], f"experiment {exp}: allocation.mode")
    for name, part in (allocation.get("partitions") or {}).items():
        if isinstance(part, dict) and "mode" in part:
            _check_mode(part["mode"], f"experiment {exp}: partition {name!r} mode")


def _has_token(value: Any) -> bool:
    """True if a `{var}` placeholder (substituted later by SbatchMan) is anywhere in value."""
    if isinstance(value, str):
        return "{" in value
    if isinstance(value, dict):
        return any(_has_token(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_token(v) for v in value)
    return False


def _check_split(split: Any, num_apps: int, field: str, exp: str, warnings: list[str]) -> None:
    """A percentage list: one entry per app, at most 100 (the allocator's tolerance).

    Under 100 is allowed (a solo baseline on part of the nodes) but warned about.
    """
    if not isinstance(split, list):
        return
    if len(split) != num_apps:
        raise ValueError(
            f"experiment {exp}: {field} has {len(split)} entries for {num_apps} apps; "
            "give one percentage per app, in app order"
        )
    if _has_token(split):
        return
    try:
        total = sum(float(x) for x in split)
    except (TypeError, ValueError):
        raise ValueError(f"experiment {exp}: {field} must be a list of numbers") from None
    if total > 100.1:
        raise ValueError(
            f"experiment {exp}: {field} sums to {total:g}, it must not exceed 100 "
            "(each app gets its own share of the nodes)"
        )
    if total < 99.9:
        warnings.append(
            f"experiment {exp}: {field} sums to {total:g}; {100 - total:g}% of the nodes stay idle"
        )


class _Slot:
    """Stands in for an app so the real allocator can be run on paper."""

    def __init__(self, partition_id: Any, start: str) -> None:
        self.partition_id = partition_id
        self.start_string = start
        self.nodes: list[str] = []

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)


def _allocate_on_paper(
    allocation: dict[str, Any], partitions: list[Any], starts: list[str], numnodes: int
) -> list[int]:
    """Node count per app, from the same NodeAllocator call as ExperimentRunner.setup."""
    slots = [_Slot(p, s) for p, s in zip(partitions, starts, strict=True)]
    node_list = [f"n{i}" for i in range(numnodes)]
    NodeAllocator.allocate_experiment(slots, node_list, allocation)
    return [len(s.nodes) for s in slots]


def _check_allocation(
    allocation: dict[str, Any],
    apps: dict[str, Any],
    numnodes: Any,
    exp: str,
    warnings: list[str],
) -> None:
    """Split shape, partition names, then no app left without nodes."""
    keys = sorted(apps.keys(), key=lambda x: int(x) if x.isdigit() else x)  # runner.py order
    app_partitions = [apps[k].get("partition") or None for k in keys]
    app_starts = [str(apps[k].get("start", "0")) for k in keys]

    partitions = allocation.get("partitions")
    names = list(partitions) if isinstance(partitions, dict) else []
    for key, name in zip(keys, app_partitions, strict=True):
        if name is None or _has_token(name) or name in names:
            continue
        if not names:
            raise ValueError(
                f"experiment {exp}: app {key}: partition {name!r} is set, but the allocation "
                "defines no partitions (a local allocation replaces the global one); "
                "add allocation.partitions or remove the app's partition"
            )
        raise ValueError(
            f"experiment {exp}: app {key}: partition {name!r} is not one of the allocation's "
            f"partitions: {', '.join(names)}"
        )

    if names:
        for name in names:
            part = partitions[name]
            members = app_partitions.count(name)
            # The allocator uses a partition's split only when it holds two or more apps.
            if isinstance(part, dict) and members > 1:
                _check_split(
                    part.get("split"), members, f"allocation.partitions.{name}.split", exp, warnings
                )
    else:
        _check_split(allocation.get("split"), len(keys), "allocation.split", exp, warnings)

    if _has_token(allocation) or not keys:
        return
    try:
        total = int(numnodes)
    except (TypeError, ValueError):
        return  # missing or a {var} token: the engine reports a missing numnodes itself
    try:
        counts = _allocate_on_paper(allocation, app_partitions, app_starts, total)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"experiment {exp}: allocation: {exc}") from exc
    for key, count in zip(keys, counts, strict=True):
        if count == 0:
            raise ValueError(
                f"experiment {exp}: app {key} would get 0 of {total} nodes. Raise numnodes or "
                "change the split/partitions; under partitions every app needs one, and apps "
                "chained with start sN still need their own nodes (CRAB does not reuse nodes yet)"
            )


def check_config(config: dict[str, Any]) -> list[str]:
    """Raise ValueError on the first value the engine would ignore or misread.

    Returns warnings for allowed but unusual values (e.g. a split leaving nodes idle), for
    the caller to log. Options are checked as each experiment sees them: global options overlaid by its
    `local_options` (the same shallow merge as the runner).
    """
    global_opts = config.get("global_options") or {}
    warnings: list[str] = []
    for exp_name, exp in _experiments(config).items():
        opts = {**global_opts, **(exp.get("local_options") or {})}
        _check_options(opts, exp_name)
        apps = exp.get("apps") or {}
        _check_allocation(
            opts.get("allocation") or {}, apps, global_opts.get("numnodes"), exp_name, warnings
        )
        for app_key, app in (exp.get("apps") or {}).items():
            where = f"experiment {exp_name}: app {app_key}"
            if not str(app.get("path") or "").strip():
                raise ValueError(f"{where} has no wrapper path")
            if "collect" in app:
                parse_bool(app["collect"], f"{where}: collect")
    return warnings
