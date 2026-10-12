"""Config checks about how the experiment is launched: launcher, Slurm directives, ppn, preset fit.

Called from `config_checks.check_config`. Every error is a `ValueError`.
"""

from __future__ import annotations

from typing import Any

from crab.core.execution.placement import app_ppn, parse_ppn
from crab.core.execution.settings import LAUNCHER_KINDS, ExecutionSettings
from crab.core.execution.tokens import has_token

_TASKS_TAIL = "CRAB sets the task count from numnodes and ppn"
# Slurm directive keys that CRAB writes itself (SlurmScheduler.generate_header drops them), with
# what to say instead.
MANAGED_DIRECTIVES: dict[str, str] = {
    "nodes": "use numnodes",
    "N": "use numnodes",
    "ntasks-per-node": "use ppn",
    "ntasks": _TASKS_TAIL,
    "n": _TASKS_TAIL,
}


def check_launcher_options(opts: dict[str, Any], exp: str) -> None:
    launcher = opts.get("launcher")
    if "launcher" in opts and not has_token(launcher) and launcher not in LAUNCHER_KINDS:
        raise ValueError(
            f"experiment {exp}: launcher {launcher!r} must be one of {', '.join(LAUNCHER_KINDS)}"
        )
    flags = opts.get("launcher_flags")
    if "launcher_flags" in opts and not has_token(flags):
        if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
            raise ValueError(f"experiment {exp}: launcher_flags must be a list of strings")


def _directive_keys(directives: Any) -> list[tuple[str, str]]:
    """(text, key) per directive, parsed as SlurmScheduler.generate_header parses them."""
    if isinstance(directives, dict):
        return [(f"{k}: {v}", str(k).lstrip("-")) for k, v in directives.items() if v is not False]
    if not isinstance(directives, list):
        return []
    found = []
    for raw in directives:
        clean = str(raw).strip().lstrip("-")
        if not clean:
            continue
        key = clean.split("=")[0] if "=" in clean else clean.split()[0]
        found.append((str(raw).strip(), key))
    return found


def check_managed_directives(global_opts: dict[str, Any]) -> None:
    """Refuse Slurm directives CRAB sets itself, from the config and from the preset's sbatch."""
    for option, source in (
        ("sbatch_directives", "sbatch_directives"),
        ("system_sbatch", "the preset's sbatch"),
    ):
        for text, key in _directive_keys(global_opts.get(option)):
            if key in MANAGED_DIRECTIVES:
                raise ValueError(
                    f"{source}: {text!r} sets {key!r}, which CRAB manages itself: "
                    f"{MANAGED_DIRECTIVES[key]}"
                )


def check_ppn(options: dict[str, Any], where: str) -> None:
    """The `ppn` (ranks per node) of the global options, `local_options` or an app: a positive
    integer or a `{var}` token. A missing one is fine."""
    if "ppn" in options and not has_token(options["ppn"]):
        try:
            parse_ppn(options["ppn"])
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from None


def _app_ppns(apps: dict[str, Any], opts: dict[str, Any]) -> list[int]:
    """The effective ppn of each app; an app whose ppn is a `{var}` token is left out."""
    ppns = []
    for app in apps.values():
        try:
            ppns.append(app_ppn(app, opts))
        except ValueError:
            continue
    return ppns


def _count(value: Any) -> int | None:
    """An integer option, or None when it is missing or a `{var}` token."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_preset_fit(
    opts: dict[str, Any],
    global_opts: dict[str, Any],
    apps: dict[str, Any],
    settings: ExecutionSettings,
    exp: str,
    warnings: list[str],
) -> None:
    """Refuse a config the preset cannot run; warn about options a local preset ignores.

    The launcher is the experiment's option, else the preset's. A receipt's launcher override is
    checked at setup, not here. `apps` is the experiment's apps: any app's ppn above 1 is too many
    ranks for the direct launcher.
    """
    local = settings.scheduler == "local"
    launcher = opts.get("launcher", settings.launcher)
    numnodes = _count(global_opts.get("numnodes"))
    problems = []
    if launcher == "srun" and local:
        problems.append("the srun launcher needs a Slurm preset")
    if launcher == "direct" and ((numnodes or 1) > 1 or any(p > 1 for p in _app_ppns(apps, opts))):
        problems.append(
            "the direct launcher runs a single process; use ppn 1 and numnodes 1, "
            "or the mpirun launcher"
        )
    if local and numnodes is not None and numnodes > len(settings.hosts):
        problems.append(
            f"numnodes {numnodes} is more than the preset's {len(settings.hosts)} host(s)"
        )
    if problems:
        raise ValueError(f"experiment {exp}: " + "; ".join(problems))
    note = "sbatch_directives are ignored by the local scheduler"
    if local and global_opts.get("sbatch_directives") and note not in warnings:
        warnings.append(note)
