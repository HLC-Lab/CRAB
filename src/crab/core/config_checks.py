"""Checks that turn silently ignored config values into errors before anything runs.

Only rules the engine would otherwise get wrong live here. Everything else stays permissive,
because configs are hand-edited and unknown app keys are wrapper attributes.
"""

from __future__ import annotations

from typing import Any

from crab.core.allocation.chains import resolve_chains
from crab.core.execution.launch_checks import (
    check_launcher_options,
    check_managed_directives,
    check_ppn,
    check_preset_fit,
)
from crab.core.execution.placement_checks import allocate_on_paper, check_experiment
from crab.core.execution.settings import ExecutionSettings
from crab.core.execution.tokens import has_token

ALLOCATION_MODES = ("linear", "interleaved", "random", "shared")
CORES_MODES = ("disjoint", "shared")
OUTPUT_FORMATS = ("csv",)
_BOOL_OPTIONS = ("convergeall", "retain_files")
_LEGACY_ALLOCATION_KEYS = ("allocationmode", "partitionsplit", "partitionlayout", "allocationsplit")
# Slurm reads these once for the whole job, so an experiment's local_options cannot change them.
_JOB_LEVEL_OPTIONS = ("numnodes", "sbatch_directives", "walltime")
_EXPERIMENT_KEYS = ("description", "local_options", "apps")


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
    if not has_token(mode) and mode not in ALLOCATION_MODES:
        raise ValueError(f"{where} {mode!r} is not one of: {', '.join(ALLOCATION_MODES)}")


def _check_shared_scope(scope: dict[str, Any], where: str) -> None:
    """`mode: "shared"` takes no split, stride or seed; `cores` goes only with it.

    `scope` is the allocation or one partition; a `{var}` value is skipped.
    """
    mode = scope.get("mode")
    if has_token(mode):
        return
    if mode == "shared":
        for key in ("split", "stride", "seed"):
            if key in scope and not has_token(scope[key]):
                raise ValueError(
                    f"{where}: {key} cannot be used with mode 'shared' "
                    "(every app gets all the nodes)"
                )
    elif "cores" in scope and not has_token(scope["cores"]):
        raise ValueError(f"{where}: cores is only valid with mode 'shared'")
    cores = scope.get("cores")
    if "cores" in scope and not has_token(cores) and cores not in CORES_MODES:
        raise ValueError(f"{where}: cores {cores!r} is not one of: {', '.join(CORES_MODES)}")


def _check_legacy_allocation(opts: dict[str, Any], exp: str) -> None:
    found = [key for key in _LEGACY_ALLOCATION_KEYS if key in opts]
    if not found:
        return
    raise ValueError(
        f"experiment {exp}: {', '.join(found)} no longer {'exists' if len(found) == 1 else 'exist'}"
        " and would be ignored; use the `allocation` block instead. Partitions laid out "
        'interleaved: {"mode": "interleaved", "partitions": {"victim": {"share": 50}, '
        '"aggressor": {"share": 50}}}, with a "partition" name on each app ("mode": "linear" '
        'for contiguous blocks, "split" inside a partition for the apps sharing it). '
        "See Allocation fields in docs/reference/configuration.md"
    )


def _check_options(opts: dict[str, Any], exp: str) -> None:
    _check_legacy_allocation(opts, exp)
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
    _check_shared_scope(allocation, f"experiment {exp}: allocation")
    if allocation.get("mode") == "shared" and allocation.get("partitions"):
        raise ValueError(
            f"experiment {exp}: allocation.mode 'shared' cannot be used with partitions "
            "(the top-level mode lays out the partition blocks); "
            "set mode 'shared' inside a partition instead"
        )
    for name, part in (allocation.get("partitions") or {}).items():
        if isinstance(part, dict) and "mode" in part:
            _check_mode(part["mode"], f"experiment {exp}: partition {name!r} mode")
        if isinstance(part, dict):
            _check_shared_scope(part, f"experiment {exp}: partition {name!r}")


def _check_split(
    split: Any, num_apps: int, field: str, exp: str, warnings: list[str], reused: list[str]
) -> None:
    """A percentage list: one entry per app that gets a share, at most 100 (the allocator's
    tolerance). `reused` names the apps that run on another app's nodes and take no share.

    Under 100 is allowed (a solo baseline on part of the nodes) but warned about.
    """
    if not isinstance(split, list):
        return
    if len(split) != num_apps:
        note = ""
        if reused:
            who = f"apps {', '.join(reused)} run" if len(reused) > 1 else f"app {reused[0]} runs"
            note = f" ({who} on another app's nodes after it and take no share)"
        raise ValueError(
            f"experiment {exp}: {field} has {len(split)} entries for {num_apps} apps; "
            f"give one percentage per app, in app order{note}"
        )
    if has_token(split):
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


def _check_ignored_keys(exp: dict[str, Any], exp_name: str, warnings: list[str]) -> None:
    """Warn about experiment-level keys the engine never reads."""
    local = exp.get("local_options") or {}
    for key in _JOB_LEVEL_OPTIONS:
        if key in local:
            warnings.append(
                f"experiment {exp_name}: local_options.{key} is ignored; it applies to the "
                "whole job and is set in global_options"
            )
    for key in exp:
        if key not in _EXPERIMENT_KEYS:
            warnings.append(
                f"experiment {exp_name}: {key} is ignored here; an experiment takes only "
                f"{', '.join(_EXPERIMENT_KEYS)}"
            )


def _check_collected_end(app: dict[str, Any], where: str, warnings: list[str]) -> None:
    """Warn when a collected app is stopped by `end`: CRAB kills it, and a killed app's
    output is skipped without a message (collect_run ignores a non-zero return code)."""
    end = app.get("end")
    if end is None or str(end).strip() == "" or has_token(end):
        return
    if not parse_bool(app.get("collect", False), f"{where}: collect"):
        return
    warnings.append(
        f"{where}: collect is on and end is {str(end).strip()!r}; if the app is still running "
        "when it is stopped, its output is not collected for that run"
    )


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
        if name is None or has_token(name) or name in names:
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

    # Apps that run on another app's nodes (start sN, ADR-032) take no share of any split.
    reuse: dict[int, int] = {}
    if not has_token(app_starts):
        try:
            reuse = resolve_chains(app_starts, app_partitions)
        except ValueError as exc:
            raise ValueError(f"experiment {exp}: {exc}") from exc
    reused = [keys[i] for i in sorted(reuse)]
    head_partitions = [p for i, p in enumerate(app_partitions) if i not in reuse]

    if names:
        for name in names:
            part = partitions[name]
            members = head_partitions.count(name)
            # The allocator uses a partition's split only when it holds two or more apps.
            if isinstance(part, dict) and members > 1:
                _check_split(
                    part.get("split"),
                    members,
                    f"allocation.partitions.{name}.split",
                    exp,
                    warnings,
                    [keys[i] for i in sorted(reuse) if app_partitions[reuse[i]] == name],
                )
    else:
        split = allocation.get("split")
        _check_split(split, len(head_partitions), "allocation.split", exp, warnings, reused)

    if has_token(allocation) or not keys:
        return
    try:
        total = int(numnodes)
    except (TypeError, ValueError):
        return  # missing or a {var} token: the engine reports a missing numnodes itself
    try:
        node_lists = allocate_on_paper(
            allocation, app_partitions, app_starts, [f"n{i}" for i in range(total)]
        )
        counts = [len(nodes) for nodes in node_lists]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"experiment {exp}: allocation: {exc}") from exc
    for key, count in zip(keys, counts, strict=True):
        if count == 0:
            raise ValueError(
                f"experiment {exp}: app {key} would get 0 of {total} nodes. Raise numnodes or "
                "change the split/partitions; under partitions every app needs one, unless it "
                "starts after another app (start sN) and runs on that app's nodes"
            )


def check_config(config: dict[str, Any], settings: ExecutionSettings | None = None) -> list[str]:
    """Raise ValueError on the first value the engine would ignore or misread.

    Returns warnings for allowed but unusual values (e.g. a split leaving nodes idle), for
    the caller to log. Options are checked as each experiment sees them: global options overlaid by its
    `local_options` (the same shallow merge as the runner). With `settings`, the config is also
    checked against what the preset can run.
    """
    global_opts = config.get("global_options") or {}
    warnings: list[str] = []
    check_managed_directives(global_opts)
    check_ppn(global_opts, "global_options")
    for exp_name, exp in _experiments(config).items():
        opts = {**global_opts, **(exp.get("local_options") or {})}
        _check_options(opts, exp_name)
        check_launcher_options(opts, exp_name)
        apps = exp.get("apps") or {}
        check_ppn(exp.get("local_options") or {}, f"experiment {exp_name}: local_options")
        for app_key, app in apps.items():
            check_ppn(app, f"experiment {exp_name}: app {app_key}")
        if settings is not None:
            check_preset_fit(opts, global_opts, apps, settings, exp_name, warnings)
        _check_ignored_keys(exp, exp_name, warnings)
        _check_allocation(
            opts.get("allocation") or {}, apps, global_opts.get("numnodes"), exp_name, warnings
        )
        placement_keys = ("start", "partition", "cpus", "env", "ppn", "nics")
        if settings is not None and not has_token(
            [opts.get("allocation"), global_opts.get("numnodes"), opts.get("ppn")]
            + [[a.get(k) for k in placement_keys] for a in apps.values()]
        ):
            warnings += check_experiment(
                exp_name,
                apps,
                opts.get("allocation") or {},
                global_opts,
                settings,
                opts.get("launcher"),
                exp.get("local_options"),
            )
        for app_key, app in (exp.get("apps") or {}).items():
            where = f"experiment {exp_name}: app {app_key}"
            if not str(app.get("path") or "").strip():
                raise ValueError(f"{where} has no wrapper path")
            if "collect" in app:
                parse_bool(app["collect"], f"{where}: collect")
            _check_collected_end(app, where, warnings)
    return warnings
