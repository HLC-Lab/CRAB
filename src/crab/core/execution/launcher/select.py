"""Picks the launcher a job runs under."""

from __future__ import annotations

from collections.abc import Mapping

from crab.core.execution.launcher.base import Launcher, UnportedLauncher
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import EnvMpirunLauncher
from crab.core.execution.launcher.srun import SrunLauncher

_ALLOWED_MODES = {"slurm", "mpi", "workerpool", "local"}


def launch_mode(environ: Mapping[str, str]) -> str:
    """The launch mode named by `CRAB_WL_MANAGER` ("slurm" when unset).

    Raises:
        ValueError: the value is not one of the known modes.
    """
    mode = environ.get("CRAB_WL_MANAGER", "slurm")
    if mode not in _ALLOWED_MODES:
        raise ValueError(
            f"Unknown CRAB_WL_MANAGER value: {mode!r}. Allowed: {sorted(_ALLOWED_MODES)}"
        )
    return mode


def launcher_for(mode: str, override: str | None, environ: Mapping[str, str]) -> Launcher:
    """The launcher for `mode`.

    `override` is the per-job launcher command (it wins over `CRAB_MPIRUN`).
    """
    if mode == "local":
        return DirectLauncher()
    if mode == "slurm":
        actual = (override or environ.get("CRAB_MPIRUN", "srun")).strip()
        # Whether it is mpirun is decided by a substring of the command, as it always was.
        if "mpirun" in actual:
            additional = environ.get("CRAB_MPIRUN_ADDITIONAL_FLAGS", "")
            map_by = environ.get("CRAB_MPIRUN_MAP_BY_NODE_FLAG", "")
            return EnvMpirunLauncher(
                tuple(actual.split()), tuple(additional.split() + map_by.split())
            )
        pinning = environ.get("CRAB_PINNING_FLAGS", "")
        return SrunLauncher(tuple(actual.split()), tuple(pinning.split()))
    return UnportedLauncher(mode)
