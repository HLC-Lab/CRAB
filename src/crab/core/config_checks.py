"""Checks that turn silently ignored config values into errors before anything runs.

Only rules the engine would otherwise get wrong live here. Everything else stays permissive,
because configs are hand-edited and unknown app keys are wrapper attributes.
"""

from __future__ import annotations

from typing import Any

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


def check_config(config: dict[str, Any]) -> None:
    """Raise ValueError on the first value the engine would ignore or misread.

    Options are checked as each experiment sees them: global options overlaid by its
    `local_options` (the same shallow merge as the runner).
    """
    global_opts = config.get("global_options") or {}
    for exp_name, exp in _experiments(config).items():
        opts = {**global_opts, **(exp.get("local_options") or {})}
        _check_options(opts, exp_name)
        for app_key, app in (exp.get("apps") or {}).items():
            where = f"experiment {exp_name}: app {app_key}"
            if not str(app.get("path") or "").strip():
                raise ValueError(f"{where} has no wrapper path")
            if "collect" in app:
                parse_bool(app["collect"], f"{where}: collect")
