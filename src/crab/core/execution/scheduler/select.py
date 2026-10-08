"""Picks the scheduler a job runs on."""

from __future__ import annotations

from crab.core.execution.scheduler.base import Scheduler
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.core.execution.settings import ExecutionSettings
from crab.log import CrabLogger


def scheduler_for(settings: ExecutionSettings, logger: CrabLogger, crab_root: str) -> Scheduler:
    """The scheduler a preset's `scheduler` field names: "local" or "slurm".

    A local scheduler's node list is the names of the preset's hosts.

    Raises:
        ValueError: `settings.scheduler` is neither.
    """
    if settings.scheduler == "local":
        return LocalScheduler(logger, crab_root, hosts=tuple(host.name for host in settings.hosts))
    if settings.scheduler == "slurm":
        return SlurmScheduler(logger, crab_root)
    raise ValueError(f"Unknown scheduler {settings.scheduler!r}; must be 'local' or 'slurm'.")
