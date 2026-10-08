"""The mpirun launcher."""

from __future__ import annotations

from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement


@dataclass(frozen=True)
class MpirunLauncher:
    """`<command> <flags> -np <ranks>`.

    Inside a Slurm allocation mpirun finds the nodes itself, so the host list is not passed.
    """

    command: tuple[str, ...]
    flags: tuple[str, ...]

    def prefix(self, placement: Placement) -> list[str]:
        return [*self.command, *self.flags, "-np", str(placement.total_ranks)]
