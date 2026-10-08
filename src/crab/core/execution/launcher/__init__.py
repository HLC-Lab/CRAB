"""Launchers: build the line that starts a job's wrapper on its hosts."""

from crab.core.execution.launcher.base import Launcher, Placement, launch_line
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import MpirunLauncher
from crab.core.execution.launcher.select import launcher_for
from crab.core.execution.launcher.srun import SrunLauncher

__all__ = [
    "DirectLauncher",
    "Launcher",
    "MpirunLauncher",
    "Placement",
    "SrunLauncher",
    "launch_line",
    "launcher_for",
]
