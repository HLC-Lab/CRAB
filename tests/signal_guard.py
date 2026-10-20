"""Pure logic of the suite-wide signal guard (installed by the autouse fixture in conftest.py).

``killpg(1, sig)`` is ``kill(-1, sig)``: every process of the user. ``kill(0, sig)`` and
``killpg(os.getpgrp(), sig)`` hit pytest's own group. A bool is an int in Python, so ``True`` is
pid 1. The shell or ``make`` that runs pytest is protected too: ``kill(os.getppid(), sig)``, the
parent's process group and pytest's session (as ``killpg(g)`` or ``kill(-g)``), and ``kill`` of the
group leader ``os.getpgrp()``. Signal 0 (a liveness probe) stays allowed on a pid. A lookup that
raises OSError or returns 0 (the PID-namespace sandbox) is unknown and refuses nothing.
``refusal`` names these calls; ``guarded`` wraps a "real" signal function so a refused call
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


def _parent_pid() -> int | None:
    """Return the parent's pid (the shell or make running pytest), or None when unknown."""
    parent = os.getppid()
    return parent if parent > 0 else None


def _parent_group() -> int | None:
    """Return the parent's process group, or None when it cannot be told."""
    parent = _parent_pid()
    if parent is None:
        return None
    try:
        group = os.getpgid(parent)
    except OSError:
        return None
    return group if group > 0 else None


def _session_id() -> int | None:
    """Return pytest's session id, or None when it cannot be told."""
    try:
        session = os.getsid(0)
    except OSError:
        return None
    return session if session > 0 else None


def _group_refusal(call: str, group: int, how: str) -> str | None:
    """Return why signalling process group ``group`` is refused, or None; ``how`` names the call."""
    if group == _own_group():
        return f"{call}: {how} of pytest's own process group"
    if group == _parent_group():
        return f"{call}: {how} of the parent's process group (the shell or make running pytest)"
    if group == _session_id():
        return f"{call}: {how} of pytest's session (the terminal running pytest)"
    return None


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
    if kind == "killpg":
        if pid <= 1:
            return f"{call}: killpg({pid}) is kill(-1), every process of the user"
        return _group_refusal(call, pid, "killpg(pgid)")
    if pid < 0:
        group_reason = _group_refusal(call, -pid, "kill(-pgid)")
        if group_reason is not None:
            return group_reason
    if pid <= 0:
        return f"{call}: kill({pid}) signals a group, and -1 is every process of the user"
    # Own pid first: inside the PID-namespace sandbox pytest is pid 1 and a probe must pass.
    if pid == os.getpid():
        return None if sig == 0 else f"{call}: kill of pytest's own pid, only signal 0 is allowed"
    if pid == 1:
        return f"{call}: kill(1) signals init"
    if sig != 0 and pid == _parent_pid():
        return f"{call}: kill of the parent pid (the shell or make running pytest)"
    if sig != 0 and pid == _own_group():
        return f"{call}: kill of the process group leader, often an ancestor of pytest"
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
