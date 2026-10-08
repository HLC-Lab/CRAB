"""Detect which MPI an `mpirun` belongs to, from its `--version` output."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Literal

Dialect = Literal["openmpi4", "openmpi5", "hydra"]

PROBE_TIMEOUT_SECONDS = 10
_HINT = "Set launchers.mpirun.command to an Open MPI or MPICH mpirun, or use srun."

# Open MPI 4 prints "OpenRTE" when the binary is not named mpirun (mpiexec, orterun).
_OPEN_MPI = re.compile(r"\((?:Open MPI|OpenRTE)\)\s+(\d+)\.(\d+)\.(\d+)")

_cache: dict[str, Dialect] = {}


def parse_version_output(text: str, exit_code: int = 0) -> Dialect:
    """Map `mpirun --version` output to a dialect.

    Args:
        text: stdout and stderr of the probe.
        exit_code: the probe's exit status.

    Raises:
        ValueError: for Intel MPI, an Open MPI older than 4, the Spack stub, or output that
            matches nothing. The message says what was found and what to set instead.
    """
    # Intel MPI is Hydra-based, so it has to be ruled out before the Hydra match.
    if "Intel(R) MPI Library" in text:
        raise ValueError(
            "Intel MPI is not supported. " + _HINT + " Supported: Open MPI 4/5, MPICH/Hydra."
        )
    if exit_code != 0 and "srun" in text:
        raise ValueError(
            "This mpirun is a stub that wants srun (Open MPI built without its launcher). "
            "Use the srun launcher, or point launchers.mpirun.command at a full Open MPI."
        )
    match = _OPEN_MPI.search(text)
    if match:
        version = ".".join(match.groups())
        if match.group(1) == "4":
            return "openmpi4"
        if match.group(1) == "5":
            return "openmpi5"
        raise ValueError(f"unsupported Open MPI version {version}: need 4.x or 5.x. {_HINT}")
    if "HYDRA build details" in text:
        return "hydra"
    head = "\n".join(text.strip().splitlines()[:3])[:200]
    if exit_code != 0:
        raise ValueError(f"mpirun --version failed with exit code {exit_code}: {head!r}. {_HINT}")
    raise ValueError(
        f"Cannot tell which MPI this mpirun is: its --version output was {head!r}. {_HINT}"
    )


def _run(argv: list[str]) -> tuple[str, int]:
    result = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return result.stdout or "", result.returncode


def probe(command: str, runner: Callable[[list[str]], tuple[str, int]] | None = None) -> Dialect:
    """Run `<command> --version` once per command and return its dialect.

    Args:
        command: the mpirun executable.
        runner: runs an argv and returns (output, exit code); defaults to a real subprocess.

    Raises:
        ValueError: when the command is missing, does not answer, or is not a supported MPI.
    """
    if command in _cache:
        return _cache[command]
    run = runner or _run
    try:
        text, code = run([command, "--version"])
    except FileNotFoundError:
        raise ValueError(f"Cannot find {command!r} to detect its MPI. {_HINT}") from None
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"{command} --version did not answer within {PROBE_TIMEOUT_SECONDS} s. {_HINT}"
        ) from None
    found = parse_version_output(text, code)
    _cache[command] = found
    return found


def clear_cache() -> None:
    """Forget every probed command (for tests)."""
    _cache.clear()
