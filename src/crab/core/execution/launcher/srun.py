"""The srun launcher."""

from __future__ import annotations

from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement


@dataclass(frozen=True)
class SrunLauncher:
    """`<command> --export=ALL --nodelist <hosts> <flags> -n <ranks> -N <hosts>`."""

    command: tuple[str, ...]
    flags: tuple[str, ...]

    def prefix(self, placement: Placement) -> list[str]:
        return [
            *self.command,
            "--export=ALL",
            "--nodelist",
            ",".join(placement.hosts),
            *self.flags,
            "-n",
            str(placement.total_ranks),
            "-N",
            str(len(placement.hosts)),
        ]
