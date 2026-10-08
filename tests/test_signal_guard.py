"""Tests for the suite-wide signal guard (tests/signal_guard.py).

Nothing here reaches the real os.kill / os.killpg with a dangerous value: the guard is built
around an injectable "real" function, and these tests pass a recorder in its place.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any

import pytest

import signal_guard
from signal_guard import guarded, refusal

OWN_PID = os.getpid()


@pytest.mark.parametrize("pgid", [True, False, 0, 1, -1, 1.0, "5", None], ids=repr)
def test_killpg_refuses_dangerous_values(pgid: Any) -> None:
    assert refusal("killpg", pgid, 15) is not None


def test_killpg_allows_an_ordinary_pgid() -> None:
    assert refusal("killpg", 424242, 15) is None


@pytest.mark.parametrize("pid", [True, 0, -1, -424242, 1, None], ids=repr)
def test_kill_refuses_dangerous_values(pid: Any) -> None:
    assert refusal("kill", pid, 15) is not None


def test_kill_allows_an_ordinary_pid() -> None:
    assert refusal("kill", 424242, 15) is None


def test_kill_own_pid_only_with_signal_zero() -> None:
    assert refusal("kill", OWN_PID, 0) is None
    assert refusal("kill", OWN_PID, 15) is not None


def test_reason_names_the_call_and_the_rule() -> None:
    reason = refusal("killpg", 1, 15)
    assert reason is not None
    assert "os.killpg(1, ...)" in reason
    assert "kill(-1)" in reason


def test_own_process_group_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getpgrp", lambda: 5000)
    assert refusal("killpg", 5000, 15) is not None
    assert refusal("kill", -5000, 15) is not None
    assert refusal("killpg", 5001, 15) is None


def test_group_zero_from_the_sandbox_skips_the_own_group_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getpgrp", lambda: 0)
    assert refusal("killpg", 424242, 15) is None


def test_guarded_refuses_before_the_real_function() -> None:
    calls: list[tuple[int, int]] = []
    wrapper = guarded("killpg", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(pytest.fail.Exception):
        wrapper(1, 15)
    assert calls == []


def test_guarded_passes_a_safe_call_through() -> None:
    calls: list[tuple[int, int]] = []
    wrapper = guarded("killpg", lambda pid, sig: calls.append((pid, sig)))
    wrapper(424242, 15)
    assert calls == [(424242, 15)]


def test_the_autouse_fixture_replaced_both_functions() -> None:
    # Inspect only: calling either with a dangerous value to find out is exactly what is forbidden.
    assert os.killpg is not signal_guard.REAL_KILLPG
    assert os.kill is not signal_guard.REAL_KILL
    assert getattr(os.killpg, "signal_guard_kind", None) == "killpg"
    assert getattr(os.kill, "signal_guard_kind", None) == "kill"


def test_a_real_process_group_can_be_signalled_through_the_guard() -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


@pytest.fixture
def fixed_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin pytest's own pid, group leader, parent, the parent's group and the session to numbers."""
    monkeypatch.setattr(os, "getpid", lambda: 5001)
    monkeypatch.setattr(os, "getpgrp", lambda: 5000)
    monkeypatch.setattr(os, "getppid", lambda: 7000)
    monkeypatch.setattr(os, "getpgid", lambda pid: 7000)
    monkeypatch.setattr(os, "getsid", lambda pid: 6000)


@pytest.mark.usefixtures("fixed_lookups")
def test_kill_of_the_parent_pid_is_refused_except_signal_zero() -> None:
    reason = refusal("kill", 7000, 15)
    assert reason is not None
    assert "os.kill(7000, ...)" in reason
    assert "parent" in reason
    assert refusal("kill", 7000, 0) is None


@pytest.mark.usefixtures("fixed_lookups")
def test_the_parents_process_group_is_refused() -> None:
    reason = refusal("killpg", 7000, 15)
    assert reason is not None
    assert "parent" in reason
    assert refusal("kill", -7000, 15) is not None


@pytest.mark.usefixtures("fixed_lookups")
def test_the_session_is_refused() -> None:
    reason = refusal("killpg", 6000, 15)
    assert reason is not None
    assert "session" in reason
    assert refusal("kill", -6000, 15) is not None


@pytest.mark.usefixtures("fixed_lookups")
def test_kill_of_the_group_leader_is_refused_except_signal_zero() -> None:
    reason = refusal("kill", 5000, 15)
    assert reason is not None
    assert "group leader" in reason
    assert refusal("kill", 5000, 0) is None


@pytest.mark.usefixtures("fixed_lookups")
def test_the_new_rules_leave_an_ordinary_pid_alone() -> None:
    assert refusal("kill", 424242, 15) is None
    assert refusal("killpg", 424242, 15) is None


@pytest.mark.usefixtures("fixed_lookups")
def test_group_leader_that_is_pytests_own_pid_keeps_the_own_pid_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getpid", lambda: 5000)
    assert refusal("kill", 5000, 0) is None
    assert refusal("kill", 5000, 15) is not None


@pytest.mark.usefixtures("fixed_lookups")
def test_a_lookup_of_zero_is_unknown_and_does_not_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getppid", lambda: 0)
    monkeypatch.setattr(os, "getsid", lambda pid: 0)
    assert refusal("killpg", 424242, 15) is None
    assert refusal("kill", 424242, 15) is None


@pytest.mark.usefixtures("fixed_lookups")
def test_a_lookup_that_raises_is_unknown_and_does_not_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raises(pid: int) -> int:
        raise OSError("no such process")

    monkeypatch.setattr(os, "getpgid", raises)
    assert refusal("killpg", 424242, 15) is None
    assert refusal("kill", 424242, 15) is None
    assert refusal("killpg", 7000, 15) is None
    monkeypatch.setattr(os, "getsid", raises)
    assert refusal("killpg", 6000, 15) is None
