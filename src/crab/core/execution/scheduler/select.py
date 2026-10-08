"""Picks the scheduler a job runs on."""

from __future__ import annotations

from collections.abc import Mapping

from crab.core.execution.scheduler.base import Scheduler
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.log import CrabLogger


def scheduler_for(environ: Mapping[str, str], logger: CrabLogger, crab_root: str) -> Scheduler:
    """The local scheduler when `CRAB_SCHEDULER=local`, Slurm otherwise."""
    if environ.get("CRAB_SCHEDULER") == "local":
        return LocalScheduler(logger, crab_root)
    return SlurmScheduler(logger, crab_root)
