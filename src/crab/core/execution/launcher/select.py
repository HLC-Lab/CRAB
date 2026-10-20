"""Picks the launcher a job runs under."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import cast

from crab.core.execution.launcher import dialect
from crab.core.execution.launcher.base import Launcher
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import MpirunLauncher
from crab.core.execution.launcher.srun import SrunLauncher
from crab.core.execution.settings import LauncherSpec


def _exported_names(spec: LauncherSpec, environ: Mapping[str, str]) -> tuple[str, ...]:
    """The spec's export names that are set in `environ`, then every other `CRAB_*` variable.

    `CRAB_SUPERVISOR_FD` is the supervisor's descriptor number and means nothing to a remote rank.
    """
    names = [name for name in spec.export if name in environ]
    names += sorted(
        name for name in environ if name.startswith("CRAB_") and name not in spec.export
    )
    return tuple(name for name in names if name != "CRAB_SUPERVISOR_FD")


def launcher_for(
    spec: LauncherSpec,
    environ: Mapping[str, str] = os.environ,
    extra_export: tuple[str, ...] = (),
) -> Launcher:
    """The launcher a resolved spec describes.

    An mpirun launcher exports the spec's names that are set in `environ`, then every other
    `CRAB_*` variable (the supervisor's descriptor never), then the names in `extra_export` that
    are not there yet: those are set by the app's script, not in `environ`, so they are not
    filtered.

    An mpirun launcher always asks for a binding report (`report_bindings=True`).

    An mpirun spec with dialect `auto` asks `dialect.probe` (cached per command) which MPI the
    command belongs to; any other dialect is used as given.

    Raises:
        ValueError: the spec's kind is not `srun`, `mpirun` or `direct`, or the probe cannot tell
            which MPI the mpirun command belongs to.
    """
    if spec.kind == "srun":
        return SrunLauncher(spec.command, spec.flags)
    if spec.kind == "mpirun":
        # Called through the module so tests can replace `dialect.probe`.
        found = dialect.probe(spec.command[0]) if spec.dialect == "auto" else spec.dialect
        names = _exported_names(spec, environ)
        return MpirunLauncher(
            spec.command,
            spec.flags,
            cast(dialect.Dialect, found),
            (*names, *(name for name in extra_export if name not in names)),
            report_bindings=True,
        )
    if spec.kind == "direct":
        return DirectLauncher()
    raise ValueError(f"Unknown launcher kind: {spec.kind!r}.")
