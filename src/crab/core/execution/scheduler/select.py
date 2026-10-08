"""Picks the scheduler a job runs on."""

from __future__ import annotations

from crab.core.execution.scheduler.base import Scheduler
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.log import CrabLogger


def scheduler_for(scheduler: str, logger: CrabLogger, crab_root: str) -> Scheduler:
    """The scheduler a preset's `scheduler` field names: "local" or "slurm".

    Raises:
        ValueError: `scheduler` is neither.
    """
    if scheduler == "local":
        return LocalScheduler(logger, crab_root)
    if scheduler == "slurm":
        return SlurmScheduler(logger, crab_root)
    raise ValueError(f"Unknown scheduler {scheduler!r}; must be 'local' or 'slurm'.")
