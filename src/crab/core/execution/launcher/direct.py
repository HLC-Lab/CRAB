"""The direct launcher: no wrapper around the command."""

from __future__ import annotations

from crab.core.execution.launcher.base import Placement


class DirectLauncher:
    """Runs the command as is: single process, no srun or mpirun (dev and testing)."""

    def prefix(self, placement: Placement) -> list[str]:
        return []
