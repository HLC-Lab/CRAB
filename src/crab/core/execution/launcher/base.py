"""The launcher interface: what builds the line that starts a job's wrapper on its hosts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Placement:
    """Where a job's ranks run: the hosts and how many ranks each one gets."""

    hosts: tuple[str, ...]
    ranks_per_host: int

    @property
    def total_ranks(self) -> int:
        return self.ranks_per_host * len(self.hosts)


class Launcher(Protocol):
    """Builds the launch prefix for one placement."""

    def prefix(self, placement: Placement) -> list[str]:
        """The launcher words that go before the wrapper's command string."""
        ...


class UnportedLauncher:
    """Stands in for a launch mode that has not been ported yet; building a line raises."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def prefix(self, placement: Placement) -> list[str]:
        raise NotImplementedError(
            f"The {self.mode!r} launch mode has not been ported to the launcher interface. "
            "Use the 'slurm' launch mode."
        )


def launch_line(launcher: Launcher, placement: Placement, command: str) -> str:
    """The full launch line: the prefix and the command, runs of whitespace collapsed to one
    space (also inside quoted arguments, as the launch string has always been built).

    With an empty prefix the command is returned exactly as given.
    """
    prefix = launcher.prefix(placement)
    if not prefix:
        return command
    return " ".join(" ".join([*prefix, command]).split())
