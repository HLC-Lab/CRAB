"""The scheduler interface: what the engine and the CLI contract ask of a workload scheduler."""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class JobStatus:
    """The state of one job, as a scheduler reports it."""

    job_id: str
    state: str
    source: str
    exit_code: str | None = None


@dataclass(frozen=True)
class CancelResult:
    """Whether a cancel request took effect, with an explanation when it did not."""

    cancelled: bool
    detail: str | None


def worker_command(job_dir: str) -> str:
    """The shell command that runs a job's worker: `<python> <crab> worker --workdir <dir>`."""
    return (
        f"{shlex.quote(sys.executable)} "
        f"{shlex.quote(os.path.abspath(sys.argv[0]))} "
        f"worker --workdir {shlex.quote(job_dir)}"
    )


class Scheduler(Protocol):
    """A workload scheduler CRAB can submit to, query and cancel on."""

    def submit(self, job_dir: str, global_opts: dict[str, Any]) -> str | None:
        """Write the job script into `job_dir`, submit it and return the job id (None if the
        scheduler's reply carried none)."""
        ...

    def status(self, ids: list[str]) -> list[JobStatus]:
        """Report the state of each job id."""
        ...

    def cancel(self, job_id: str) -> CancelResult:
        """Cancel one job."""
        ...

    def node_list(self) -> list[str]:
        """The hostnames of the current allocation. Called inside the worker."""
        ...

    def describe_nodes(self) -> dict[str, Any]:
        """The cluster's nodes and partitions, for `crab nodes`."""
        ...
