"""Launchers: build the line that starts a job's wrapper on its hosts."""

from crab.core.execution.launcher.base import Launcher, Placement, UnportedLauncher, launch_line
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import EnvMpirunLauncher
from crab.core.execution.launcher.select import launch_mode, launcher_for
from crab.core.execution.launcher.srun import SrunLauncher

__all__ = [
    "DirectLauncher",
    "EnvMpirunLauncher",
    "Launcher",
    "Placement",
    "SrunLauncher",
    "UnportedLauncher",
    "launch_line",
    "launch_mode",
    "launcher_for",
]
