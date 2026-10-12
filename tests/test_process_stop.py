"""stop_processes: SIGTERM to each app's process group, one shared grace, then SIGKILL.

Real children are started here with ``start_new_session=True`` (the pid is the group id, as for
``run_job``) and killed in a ``finally``. Fabricated pids only ever reach a recorder that
replaces ``os.killpg``.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.process import manager
from crab.core.process.manager import STOP_GRACE_SECONDS, stop_processes
from signal_guard import REAL_KILLPG

IGNORE_TERM = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
# Ignores SIGTERM, then tells the test its pid; argv[1] is the pid file.
IGNORE_SCRIPT = """\
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))
time.sleep(30)
"""
NEEDS_PROC = pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="needs /proc (Linux)")


def _live_members(pgid: int) -> list[int] | None:
    """Non-zombie members of process group `pgid` from /proc, or None off Linux."""
    proc = Path("/proc")
    if not (proc / "self" / "stat").is_file():
        return None
    members: list[int] = []
    for stat in proc.glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
        except OSError:
            continue  # the process exited while we scanned
        state, _ppid, group = text[text.rindex(")") + 2 :].split()[:3]
        if int(group) == pgid and state != "Z":
            members.append(int(stat.parent.name))
    return members


@contextmanager
def _children(*commands: list[str]) -> Iterator[list[subprocess.Popen]]:
    """Start each command in its own session; on exit SIGKILL every group and reap."""
    procs = [subprocess.Popen(c, start_new_session=True) for c in commands]
    try:
        yield procs
    finally:
        for proc in procs:
            try:
                REAL_KILLPG(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()


def _wait_until(check: Callable[[], bool], what: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _state(pid: int) -> str | None:
    """Process state letter from /proc, or None when the process is gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return text[text.rindex(")") + 2 :].split()[0]


def _alive(pid: int) -> bool:
    """Whether `pid` exists and is not a zombie."""
    return _state(pid) not in (None, "Z", "X")


def _wrapper_with_ignoring_child(tmp_path: Path, name: str) -> tuple[list[str], Path]:
    """A `bash -c` wrapper whose child ignores SIGTERM, and the file the child writes its pid to."""
    script = tmp_path / "ignore.py"
    script.write_text(IGNORE_SCRIPT)
    pid_file = tmp_path / f"{name}.pid"
    python, script_arg, pid_arg = (
        shlex.quote(sys.executable),
        shlex.quote(str(script)),
        shlex.quote(str(pid_file)),
    )
    return ["bash", "-c", f"{python} {script_arg} {pid_arg} & wait"], pid_file


def _child_pid(pid_file: Path) -> int:
    _wait_until(lambda: pid_file.exists() and pid_file.read_text() != "", "the child's pid file")
    return int(pid_file.read_text())


def _logged(logger: MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in logger.method_calls if call.args)


def test_default_grace_is_ten_seconds() -> None:
    assert STOP_GRACE_SECONDS == 10.0


def test_a_child_that_exits_on_sigterm_is_stopped_before_the_grace() -> None:
    logger = MagicMock()
    with _children(["sleep", "30"]) as (proc,):
        start = time.monotonic()
        stop_processes([proc], logger, grace=5)
        elapsed = time.monotonic() - start

        assert elapsed < 2
        assert proc.returncode == -signal.SIGTERM
    assert "SIGKILL" not in _logged(logger)


def test_a_child_that_ignores_sigterm_is_killed_after_the_grace() -> None:
    logger = MagicMock()
    with _children([sys.executable, "-c", IGNORE_TERM]) as (proc,):
        # Let the child install its handler; SIGTERM before that would just kill it.
        time.sleep(0.5)
        start = time.monotonic()
        stop_processes([proc], logger, grace=0.5)
        elapsed = time.monotonic() - start

        assert 0.4 < elapsed < 2
        assert proc.returncode == -signal.SIGKILL
    assert f"PID {proc.pid}: still running after 0.5s, sending SIGKILL" in _logged(logger)


@pytest.mark.skipif(_live_members(os.getpgrp()) is None, reason="needs /proc (Linux)")
def test_the_whole_group_is_stopped() -> None:
    with _children(["sh", "-c", "sleep 30 & sleep 30 & wait"]) as (proc,):
        _wait_until(lambda: len(_live_members(proc.pid) or []) >= 3, "the group to fill")

        stop_processes([proc], MagicMock(), grace=5)

        _wait_until(lambda: _live_members(proc.pid) == [], "the group to disappear")


def test_two_processes_share_one_grace() -> None:
    logger = MagicMock()
    with _children([sys.executable, "-c", IGNORE_TERM], [sys.executable, "-c", IGNORE_TERM]) as (
        first,
        second,
    ):
        time.sleep(0.5)
        start = time.monotonic()
        stop_processes([first, second], logger, grace=0.5)
        elapsed = time.monotonic() - start

        assert elapsed < 1.5
        assert first.returncode == -signal.SIGKILL
        assert second.returncode == -signal.SIGKILL


@NEEDS_PROC
def test_an_obedient_child_of_the_wrapper_is_stopped_before_the_grace() -> None:
    with _children(["bash", "-c", "sleep 30 & wait"]) as (proc,):
        _wait_until(lambda: len(_live_members(proc.pid) or []) >= 2, "the group to fill")

        start = time.monotonic()
        stop_processes([proc], MagicMock(), grace=5)
        elapsed = time.monotonic() - start

        assert elapsed < 2
        assert _live_members(proc.pid) == []


@NEEDS_PROC
def test_a_child_of_the_wrapper_that_ignores_sigterm_is_killed_after_the_grace(
    tmp_path: Path,
) -> None:
    logger = MagicMock()
    command, pid_file = _wrapper_with_ignoring_child(tmp_path, "one")
    with _children(command) as (proc,):
        child = _child_pid(pid_file)

        start = time.monotonic()
        stop_processes([proc], logger, grace=1)
        elapsed = time.monotonic() - start

        assert 0.9 < elapsed < 2.5
        # SIGKILL is delivered, not yet reaped, when stop_processes returns.
        _wait_until(lambda: not _alive(child), "the child to die")
        assert _live_members(proc.pid) == []
    assert f"PID {proc.pid}: still running after 1s, sending SIGKILL" in _logged(logger)


@NEEDS_PROC
def test_two_wrappers_with_stubborn_children_share_one_grace(tmp_path: Path) -> None:
    first_cmd, first_file = _wrapper_with_ignoring_child(tmp_path, "first")
    second_cmd, second_file = _wrapper_with_ignoring_child(tmp_path, "second")
    with _children(first_cmd, second_cmd) as (first, second):
        children = [_child_pid(first_file), _child_pid(second_file)]

        start = time.monotonic()
        stop_processes([first, second], MagicMock(), grace=1)
        elapsed = time.monotonic() - start

        assert elapsed < 2.5
        _wait_until(lambda: not any(_alive(pid) for pid in children), "the children to die")


@NEEDS_PROC
def test_group_members_tells_a_live_group_from_a_gone_one() -> None:
    with _children(["sleep", "30"]) as (proc,):
        assert manager._group_members(proc.pid) is True

        REAL_KILLPG(proc.pid, signal.SIGKILL)
        proc.wait()

        assert manager._group_members(proc.pid) is False


@NEEDS_PROC
def test_group_members_does_not_count_a_zombie() -> None:
    with _children(["true"]) as (proc,):
        _wait_until(lambda: _state(proc.pid) == "Z", "the child to become a zombie")

        assert manager._group_members(proc.pid) is False


@pytest.mark.parametrize("pgid", [1, 0, -5, os.getpgrp()])
def test_group_members_refuses_a_group_that_could_hit_more_than_the_app(pgid: int) -> None:
    assert manager._group_members(pgid) is False


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    return calls


def test_an_already_exited_process_is_never_signalled(recorder: list[tuple[int, int]]) -> None:
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()

    stop_processes([proc], MagicMock(), grace=0.5)

    assert recorder == []


@pytest.mark.parametrize("pid", [1, 0, -5, os.getpgrp()])
def test_a_pid_that_could_hit_more_than_the_app_is_never_signalled(
    recorder: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch, pid: int
) -> None:
    # The group "has members", so stop_processes reaches the SIGKILL step and _signal_group's
    # own guard is what refuses.
    monkeypatch.setattr(manager, "_group_members", lambda pgid: True)
    fake = MagicMock()
    fake.pid = pid
    fake.poll.return_value = None
    fake.wait.return_value = 0

    stop_processes([fake], MagicMock(), grace=0.2)

    assert recorder == []


def test_a_normal_pid_gets_sigterm_then_sigkill_when_it_stays(
    recorder: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_group_members", lambda pgid: True)
    fake = MagicMock()
    fake.pid = 424242
    fake.poll.return_value = None  # never exits
    fake.wait.return_value = -9

    stop_processes([fake], MagicMock(), grace=0.2)

    assert recorder == [(424242, signal.SIGTERM), (424242, signal.SIGKILL)]


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_a_group_that_is_gone_or_not_ours_is_skipped_quietly(
    monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    calls: list[tuple[int, int]] = []

    def killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        raise error

    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(manager, "_group_members", lambda pgid: False)
    fake = MagicMock()
    fake.pid = 424242
    fake.poll.return_value = None
    fake.wait.return_value = 0

    stop_processes([fake], MagicMock(), grace=0.2)

    assert calls == [(424242, signal.SIGTERM)]


def test_stop_processes_empties_the_registry() -> None:
    from crab.core.process import manager

    with _children(["sleep", "30"]) as (proc,):
        manager.register_process(proc, "App 0")
        assert proc.pid in manager._registry

        stop_processes([proc], MagicMock(), grace=5)

        assert proc.pid not in manager._registry


def test_stop_all_registered_stops_what_is_left_and_clears_the_registry() -> None:
    from crab.core.process import manager, stop_all_registered

    logger = MagicMock()
    with _children(["sleep", "30"], [sys.executable, "-c", IGNORE_TERM]) as (term, ignoring):
        time.sleep(0.5)
        manager.register_process(term, "App 0")
        manager.register_process(ignoring, "App 1")

        stop_all_registered(logger, grace=0.5)

        assert term.returncode == -signal.SIGTERM
        assert ignoring.returncode == -signal.SIGKILL
        assert manager._registry == {}
    assert "App 1: still running after 0.5s, sending SIGKILL" in _logged(logger)


def test_wait_timed_forgets_a_process_that_exited_by_itself() -> None:
    from crab.core.process import manager, wait_timed

    proc = subprocess.Popen(
        ["true"], start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    job = MagicMock()
    job.process = proc
    job._stream_thread = None
    job.raw_stdout_buffer = []
    manager.register_process(proc, "App 0")

    assert wait_timed(job, 5, MagicMock()) is False

    assert proc.pid not in manager._registry
