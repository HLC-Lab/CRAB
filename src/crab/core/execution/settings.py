"""A preset's execution settings: parsing, the `execution.json` shape and launcher choice.

Pure apart from the file reader `from_preset` takes (it only reads a hostfile). Every error is a
`ValueError` that starts with where the problem is, such as `preset 'lab': launchers.mpirun.dialect`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from crab.core.execution.hosts import LOCALHOST, Host, parse_hostfile, parse_hosts

SCHEDULERS = ("slurm", "local")
LAUNCHER_KINDS = ("srun", "mpirun", "direct")
DIALECTS = ("auto", "openmpi4", "openmpi5", "hydra")
ALLOWED_PRESET_KEYS = (
    "description",
    "scheduler",
    "exclusive",
    "hosts",
    "hostfile",
    "launcher",
    "launchers",
    "env",
    "sbatch",
    "header",
)
DROPPED_KEYS: dict[str, str] = {
    "CRAB_WL_MANAGER": "launcher",
    "CRAB_SCHEDULER": "scheduler",
    "CRAB_MPIRUN": "launchers.mpirun.command",
    "CRAB_MPIRUN_ADDITIONAL_FLAGS": "launchers.mpirun.flags",
    "CRAB_MPIRUN_MAP_BY_NODE_FLAG": "launchers.mpirun.flags",
    "CRAB_MPIRUN_HOSTNAMES_FLAG": "nothing: CRAB passes the hosts to mpirun itself",
    "CRAB_PINNING_FLAGS": "launchers.srun.flags",
}


@dataclass(frozen=True)
class LauncherSpec:
    """How to launch: `kind` plus its command, dialect, extra flags and forwarded variables.

    `dialect` is "" and `export` is empty for srun and direct; `direct` has no command.
    """

    kind: str
    command: tuple[str, ...]
    dialect: str
    flags: tuple[str, ...]
    export: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionSettings:
    """The execution fields of one preset. `hosts` is empty under Slurm; `env` is read-only."""

    scheduler: str
    hosts: tuple[Host, ...]
    exclusive: bool
    launcher: str
    srun: LauncherSpec
    mpirun: LauncherSpec
    env: Mapping[str, str]


def _read_text(path: str) -> str:
    return Path(path).read_text()


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}: must be a list of strings.")
    return tuple(value)


def _object(value: object, where: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    """Check that `value` is an object whose keys are all in `allowed`."""
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be an object.")
    for key in value:
        if key not in allowed:
            raise ValueError(f"{where}: unknown key {key!r}; allowed: {', '.join(allowed)}.")
    return value


def check_dropped_keys(environ: Mapping[str, str], where: str) -> None:
    """Refuse the `CRAB_*` launch keys that no longer mean anything.

    Raises:
        ValueError: `environ` holds a dropped key; the message names what to use instead.
    """
    for key, replacement in DROPPED_KEYS.items():
        if key in environ:
            raise ValueError(f"{where}: {key} is no longer read; use {replacement}")


def _parse_env(value: object, where: str) -> Mapping[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(f"{where}: env: must be an object of strings.")
    for key, replacement in DROPPED_KEYS.items():
        if key in value:
            raise ValueError(f"{where}: env.{key} is no longer read; use {replacement}")
    return MappingProxyType(dict(value))


def _parse_srun(value: object, where: str) -> LauncherSpec:
    body = _object(value, where, ("flags",))
    return LauncherSpec(
        "srun", ("srun",), "", _string_list(body.get("flags", []), f"{where}.flags"), ()
    )


def _parse_mpirun(value: object, where: str) -> LauncherSpec:
    body = _object(value, where, ("command", "dialect", "flags", "export"))
    command = body.get("command", "mpirun")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"{where}.command: must be a non-empty string.")
    dialect = body.get("dialect", "auto")
    if dialect not in DIALECTS:
        raise ValueError(
            f"{where}.dialect: {dialect!r} must be one of {', '.join(DIALECTS)} "
            "(Intel MPI is not supported)."
        )
    return LauncherSpec(
        "mpirun",
        (command,),
        dialect,
        _string_list(body.get("flags", []), f"{where}.flags"),
        _string_list(body.get("export", []), f"{where}.export"),
    )


def _parse_hosts(
    body: dict[str, Any], where: str, read_file: Callable[[str], str]
) -> tuple[tuple[Host, ...], bool]:
    """Return the hosts and `exclusive` of a local preset (the defaults when neither is given)."""
    exclusive = body.get("exclusive", True)
    if not isinstance(exclusive, bool):
        raise ValueError(f"{where}: exclusive: must be true or false.")
    if "hosts" in body and "hostfile" in body:
        raise ValueError(f"{where}: hostfile: give hosts or hostfile, not both.")
    if "hosts" in body:
        return parse_hosts(body["hosts"], f"{where}: hosts"), exclusive
    if "hostfile" not in body:
        return (Host(LOCALHOST, None),), exclusive
    path = body["hostfile"]
    if not isinstance(path, str) or not (path.startswith("~") or os.path.isabs(path)):
        raise ValueError(f"{where}: hostfile: must be absolute or start with ~, got {path!r}.")
    path = os.path.expanduser(path)
    try:
        text = read_file(path)
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{where}: hostfile: cannot read {path!r}: {error}") from error
    try:
        return parse_hostfile(text, path), exclusive
    except ValueError as error:
        raise ValueError(f"{where}: hostfile: {error}") from error


def from_preset(
    preset: object, name: str, read_file: Callable[[str], str] = _read_text
) -> ExecutionSettings:
    """Validate the execution fields of one preset body and build `ExecutionSettings`.

    `description`, `sbatch` and `header` are allowed and not checked here. `read_file` reads a
    hostfile and is the only IO.

    Raises:
        ValueError: a field is invalid, a key is unknown or a dropped `CRAB_*` key is in `env`.
    """
    where = f"preset {name!r}"
    if not isinstance(preset, dict):
        raise ValueError(f"{where}: must be an object.")
    for key in preset:
        if key not in ALLOWED_PRESET_KEYS:
            raise ValueError(
                f"{where}: unknown key {key!r}; allowed keys: {', '.join(ALLOWED_PRESET_KEYS)}."
            )
    scheduler = preset.get("scheduler")
    if scheduler not in SCHEDULERS:
        raise ValueError(
            f"{where}: scheduler: must be one of {', '.join(SCHEDULERS)}, got {scheduler!r}."
        )
    if scheduler == "slurm":
        for field in ("exclusive", "hosts", "hostfile"):
            if field in preset:
                raise ValueError(
                    f"{where}: {field}: local only; the Slurm scheduler picks the nodes."
                )
        hosts: tuple[Host, ...] = ()
        exclusive = False
    else:
        hosts, exclusive = _parse_hosts(preset, where, read_file)
    launcher = preset.get("launcher", "srun" if scheduler == "slurm" else "direct")
    if launcher not in LAUNCHER_KINDS:
        raise ValueError(
            f"{where}: launcher: must be one of {', '.join(LAUNCHER_KINDS)}, got {launcher!r}."
        )
    if launcher == "srun" and scheduler == "local":
        raise ValueError(f"{where}: launcher: srun needs the slurm scheduler.")
    launchers = _object(preset.get("launchers", {}), f"{where}: launchers", ("srun", "mpirun"))
    return ExecutionSettings(
        scheduler=scheduler,
        hosts=hosts,
        exclusive=exclusive,
        launcher=launcher,
        srun=_parse_srun(launchers.get("srun", {}), f"{where}: launchers.srun"),
        mpirun=_parse_mpirun(launchers.get("mpirun", {}), f"{where}: launchers.mpirun"),
        env=_parse_env(preset.get("env", {}), where),
    )


SLURM_DEFAULT: ExecutionSettings = from_preset({"scheduler": "slurm"}, "default")
"""The settings of a worker that has no preset: Slurm, srun, no flags."""


def _host_text(host: Host) -> str:
    return host.name if host.cores is None else f"{host.name}:{host.cores}"


def to_json(settings: ExecutionSettings) -> dict[str, Any]:
    """The settings in the preset shape, with a hostfile already resolved into `hosts`."""
    data: dict[str, Any] = {"scheduler": settings.scheduler}
    if settings.scheduler == "local":
        data["exclusive"] = settings.exclusive
        data["hosts"] = [_host_text(host) for host in settings.hosts]
    data["launcher"] = settings.launcher
    data["launchers"] = {
        "srun": {"flags": list(settings.srun.flags)},
        "mpirun": {
            "command": settings.mpirun.command[0],
            "dialect": settings.mpirun.dialect,
            "flags": list(settings.mpirun.flags),
            "export": list(settings.mpirun.export),
        },
    }
    data["env"] = dict(settings.env)
    return data


def from_json(data: object) -> ExecutionSettings:
    """Read the `execution.json` a submit wrote: the preset shape, parsed by `from_preset`."""
    return from_preset(data, "execution.json")


def resolve_launcher(
    settings: ExecutionSettings, exp_opts: Mapping[str, Any], receipt_override: str | None
) -> LauncherSpec:
    """Pick an experiment's launcher.

    The kind comes from the receipt's `launcher_override`, else `exp_opts["launcher"]`, else the
    preset. `exp_opts["launcher_flags"]` replaces the flags of the kind the options or the preset
    chose; a kind the receipt switched to keeps the preset's flags.

    Raises:
        ValueError: an override or option is invalid, or the result is `srun` under a local scheduler.
    """
    chosen = exp_opts.get("launcher", settings.launcher)
    if chosen not in LAUNCHER_KINDS:
        raise ValueError(
            f"options.launcher: must be one of {', '.join(LAUNCHER_KINDS)}, got {chosen!r}."
        )
    flags: tuple[str, ...] | None = None
    if "launcher_flags" in exp_opts:
        flags = _string_list(exp_opts["launcher_flags"], "options.launcher_flags")
    kind = chosen
    if receipt_override:
        if receipt_override not in ("srun", "mpirun"):
            raise ValueError(
                f'The receipt\'s launcher_override must be "srun" or "mpirun", got {receipt_override!r}.'
            )
        kind = receipt_override
    if kind == "srun" and settings.scheduler == "local":
        raise ValueError("The srun launcher needs the slurm scheduler, but this preset is local.")
    if kind == "direct":
        return LauncherSpec("direct", (), "", (), ())
    spec = settings.srun if kind == "srun" else settings.mpirun
    if flags is not None and kind == chosen:
        return LauncherSpec(spec.kind, spec.command, spec.dialect, flags, spec.export)
    return spec
