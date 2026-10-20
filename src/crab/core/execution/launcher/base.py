"""The launcher interface: what builds the line that starts a job's wrapper on its hosts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Placement:
    """Where a job's ranks run: the hosts, how many ranks each one gets, and their cores.

    `cpus` is the explicit core list every host's ranks are bound to (one core per rank);
    `bind_none` asks the launcher not to bind at all (an app on shared cores with no list), and
    `cpus` wins when both are given. Neither means the launcher's own default.
    `co_run` is set when the app's nodes are shared with another app of the same experiment
    running at the same time ("shared" when the allocation says `cores: "shared"`).
    `job_ranks_per_host` is the job's tasks per node from the sbatch header.
    """

    hosts: tuple[str, ...]
    ranks_per_host: int
    cpus: tuple[int, ...] | None = None
    bind_none: bool = False
    co_run: Literal["disjoint", "shared"] | None = None
    job_ranks_per_host: int | None = None

    @property
    def total_ranks(self) -> int:
        return self.ranks_per_host * len(self.hosts)


class Launcher(Protocol):
    """Builds the launch prefix for one placement."""

    def prefix(self, placement: Placement) -> list[str]:
        """The launcher words that go before the wrapper's command string."""
        ...


def launch_line(launcher: Launcher, placement: Placement, command: str) -> str:
    """The full launch line: the prefix and the command, runs of whitespace collapsed to one
    space (also inside quoted arguments, as the launch string has always been built).

    With an empty prefix the command is returned exactly as given.
    """
    prefix = launcher.prefix(placement)
    if not prefix:
        return command
    return " ".join(" ".join([*prefix, command]).split())
