"""Pure logic of the suite-wide signal guard (installed by the autouse fixture in conftest.py).

``killpg(1, sig)`` is ``kill(-1, sig)``: every process of the user. ``kill(0, sig)`` and
``killpg(os.getpgrp(), sig)`` hit pytest's own group. A bool is an int in Python, so ``True`` is
pid 1. ``refusal`` names these calls; ``guarded`` wraps a "real" signal function so a refused call
fails the test before the real function is reached.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest

# Captured at import time, before any test patches os, so the fixture and the tests share them.
REAL_KILL: Callable[[int, int], None] = os.kill
REAL_KILLPG: Callable[[int, int], None] = os.killpg


def _own_group() -> int | None:
    """Return pytest's own process group, or None when it cannot be told (0 in the sandbox)."""
    group = os.getpgrp()
    return group if group > 0 else None


def refusal(kind: str, pid: object, sig: object = None) -> str | None:
    """Return why ``os.<kind>(pid, sig)`` must be refused, or None when it is safe.

    Args:
        kind: ``"kill"`` or ``"killpg"``.
        pid: the pid (kill) or process group id (killpg) the test passed.
        sig: the signal; only ``kill(os.getpid(), 0)`` (a liveness probe) is allowed on itself.
    """
    call = f"test tried os.{kind}({pid!r}, ...)"
    if type(pid) is not int:
        return f"{call}: {kind} needs a plain int, got {type(pid).__name__}"
    own_group = _own_group()
    if kind == "killpg":
        if pid <= 1:
            return f"{call}: killpg({pid}) is kill(-1), every process of the user"
        if pid == own_group:
            return f"{call}: killpg(pid) of pytest's own process group"
        return None
    if own_group is not None and -pid == own_group:
        return f"{call}: kill(-pgid) of pytest's own process group"
    if pid <= 0:
        return f"{call}: kill({pid}) signals a group, and -1 is every process of the user"
    # Own pid first: inside the PID-namespace sandbox pytest is pid 1 and a probe must pass.
    if pid == os.getpid():
        return None if sig == 0 else f"{call}: kill of pytest's own pid, only signal 0 is allowed"
    if pid == 1:
        return f"{call}: kill(1) signals init"
    return None


def guarded(kind: str, real: Callable[[int, int], None]) -> Callable[[int, int], None]:
    """Wrap ``real`` so a call that ``refusal`` objects to fails the test instead of running.

    ``pytest.fail`` raises an OutcomeException, a BaseException: the ``except OSError`` and
    ``except Exception`` handlers in the code under test do not swallow it.
    """

    def wrapper(pid: int, sig: int) -> None:
        reason = refusal(kind, pid, sig)
        if reason is not None:
            pytest.fail(reason, pytrace=False)
        real(pid, sig)

    wrapper.signal_guard_kind = kind  # type: ignore[attr-defined]
    return wrapper
