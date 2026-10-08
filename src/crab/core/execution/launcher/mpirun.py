"""The mpirun launcher, driven by environment variables."""

from __future__ import annotations

from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement


@dataclass(frozen=True)
class EnvMpirunLauncher:
    """`<command> <flags> -np <ranks>`.

    Reproduces the env-driven mpirun line as it has always been built; plan 095 step C2
    replaces this file with per-implementation dialects. Inside a Slurm allocation mpirun finds
    the nodes itself, so the host list is not passed.
    """

    command: tuple[str, ...]
    flags: tuple[str, ...]

    def prefix(self, placement: Placement) -> list[str]:
        return [*self.command, *self.flags, "-np", str(placement.total_ranks)]
