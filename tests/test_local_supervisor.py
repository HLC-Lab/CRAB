"""Tests for the local job supervisor, with REAL processes.

`LocalScheduler.submit` starts a detached supervisor (`scheduler/supervisor.py`) that holds the job's
liveness lock, writes `local_state.json` and `local_exit_code`, and runs the worker. The worker is
replaced by a short shell command (`worker_argv` is injectable). Every supervisor a test starts is
recorded, and its process group is killed (SIGKILL, through the signal guard) and reaped in the
fixture teardown, so no test leaves a process behind. Signals only ever go to those groups.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution.scheduler import local_state, supervisor
from crab.core.execution.scheduler.base import JobStatus
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.settings import from_preset, to_json
from signal_guard import REAL_KILLPG, guarded

DEADLINE = 15.0
_killpg = guarded("killpg", REAL_KILLPG)


def _poll(check: Callable[[], Any], what: str, timeout: float = DEADLINE) -> Any:
    """Call `check` until it returns something truthy; fail with `what` after the deadline."""
    end = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= end:
            pytest.fail(f"timed out after {timeout}s waiting for: {what}", pytrace=False)
        time.sleep(0.02)


def _live_members(pgid: int) -> list[int] | None:
    """The pids of the non-zombie processes in group `pgid`, or None without a /proc to read.

    A dead worker is reparented when its supervisor dies: under the sandbox (a PID namespace)
    to pytest itself, which never reaps it. A zombie still counts for `killpg(pgid, 0)`, so the
    group is judged by its live members.
    """
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


def _group_gone(pid: int) -> bool:
    """Whether the process group `pid` has no live process left; the supervisor, our child, is
    reaped first (a zombie supervisor would keep `killpg(pid, 0)` succeeding)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    members = _live_members(pid)
    if members is not None:
        return not members
    try:
        _killpg(pid, 0)  # never the patched `os.killpg`: a test may have replaced it
    except ProcessLookupError:
        return True
    return False


class Harness:
    """Submits jobs that run short shell commands and remembers every supervisor it started."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.jobs = local_state.jobs_dir({"XDG_STATE_HOME": str(tmp_path / "xdg-state")})
        self.supervisors: list[int] = []
        self.worker_groups: list[int] = []  # workers run in their own group: killed at teardown

    def scheduler(self, cmd: list[str] | Callable[[str], list[str]]) -> LocalScheduler:
        return LocalScheduler(
            MagicMock(),
            str(self.tmp_path / "root"),
            environ={"XDG_STATE_HOME": str(self.tmp_path / "xdg-state")},
            worker_argv=cmd if callable(cmd) else lambda job_dir: cmd,
        )

    def submit(
        self,
        name: str,
        cmd: list[str] | Callable[[str], list[str]],
        exclusive: bool | None = None,
        relative: bool = False,
    ) -> tuple[str, Path]:
        """Submit `cmd` as job directory `name`; `exclusive` False writes an `execution.json`.

        `relative` hands the scheduler the name itself (the test chdir-ed into `tmp_path`)."""
        job_dir = self.tmp_path / name
        job_dir.mkdir()
        if exclusive is not None:
            preset = {"scheduler": "local", "exclusive": exclusive}
            (job_dir / "execution.json").write_text(json.dumps(to_json(from_preset(preset, "t"))))
        job_id = self.scheduler(cmd).submit(name if relative else str(job_dir), {})
        assert job_id is not None
        record = local_state.read_record(self.jobs, job_id)
        assert record is not None
        self.supervisors.append(record.supervisor_pid)
        return job_id, job_dir

    def status(self, job_id: str) -> JobStatus:
        return self.scheduler(["true"]).status([job_id])[0]

    def pid(self, job_id: str) -> int:
        record = local_state.read_record(self.jobs, job_id)
        assert record is not None
        return record.supervisor_pid

    def kill_all(self) -> None:
        groups = self.supervisors + self.worker_groups
        for pid in groups:
            try:
                _killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in groups:
            _poll(lambda pid=pid: _group_gone(pid), f"group {pid} to disappear", timeout=5)


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.kill_all()


def _final(h: Harness, job_id: str) -> JobStatus:
    """Wait until the job is no longer PENDING or RUNNING and return its status."""
    return _poll(
        lambda: (s := h.status(job_id)).state not in ("PENDING", "RUNNING") and s,
        f"{job_id} to finish",
    )


def _pid_from(path: Path) -> int:
    """The pid a worker wrote to `path`, once it has (polls)."""
    return int(_poll(lambda: path.is_file() and path.read_text().strip(), f"{path.name} to exist"))


def _state_file(job_dir: Path) -> dict[str, str]:
    data: dict[str, str] = json.loads((job_dir / "local_state.json").read_text())
    return data


def test_a_job_goes_from_submitted_to_completed(harness: Harness) -> None:
    job_id, job_dir = harness.submit("job", ["sh", "-c", "echo $$ > worker.pid; sleep 1"])

    assert harness.status(job_id).state in ("PENDING", "RUNNING")
    final = _final(harness, job_id)

    assert final == JobStatus(job_id, "COMPLETED", "local", "0")
    assert _state_file(job_dir)["state"] == "COMPLETED"
    worker_pid = int((job_dir / "worker.pid").read_text())
    assert harness.pid(job_id) != worker_pid


def test_a_failing_worker_is_failed_with_its_exit_code(harness: Harness) -> None:
    job_id, job_dir = harness.submit("job", ["sh", "-c", "exit 3"])

    assert _final(harness, job_id) == JobStatus(job_id, "FAILED", "local", "3")
    assert _state_file(job_dir)["state"] == "FAILED"


def test_exclusive_jobs_on_one_host_run_one_after_the_other(harness: Harness) -> None:
    first, first_dir = harness.submit("first", ["sleep", "3"])
    _poll(lambda: harness.status(first).state == "RUNNING", "the first job to run")
    second, second_dir = harness.submit("second", ["sleep", "2"])
    _poll(lambda: (second_dir / "local_state.json").is_file(), "the second job to start")

    assert harness.status(first).state == "RUNNING"
    assert harness.status(second).state == "PENDING"

    def second_running_at() -> str | None:
        try:
            body = _state_file(second_dir)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return body["at"] if body["state"] == "RUNNING" else None

    started = _poll(second_running_at, "the second job to run")
    assert _final(harness, first).state == "COMPLETED"
    assert _final(harness, second).state == "COMPLETED"
    assert started >= _state_file(first_dir)["at"]


def test_jobs_that_are_not_exclusive_run_together(harness: Harness) -> None:
    first, _ = harness.submit("first", ["sleep", "2"], exclusive=False)
    second, _ = harness.submit("second", ["sleep", "2"], exclusive=False)

    _poll(
        lambda: harness.status(first).state == "RUNNING" == harness.status(second).state,
        "both jobs to run at the same time",
    )
    assert _final(harness, first).state == "COMPLETED"
    assert _final(harness, second).state == "COMPLETED"


def test_a_killed_supervisor_reads_lost_and_cannot_be_cancelled(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, job_dir = harness.submit("job", ["sh", "-c", "echo $$ > worker.pid; exec sleep 30"])
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
    harness.worker_groups.append(_pid_from(job_dir / "worker.pid"))
    pid = harness.pid(job_id)

    os.killpg(pid, signal.SIGKILL)
    _poll(lambda: _group_gone(pid), "the killed group to disappear")

    assert harness.status(job_id) == JobStatus(job_id, "LOST", "local")
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    result = harness.scheduler(["true"]).cancel(job_id)
    assert result.cancelled is False
    assert result.detail is not None and "supervisor is gone" in result.detail
    assert calls == []


def test_cancel_stops_a_running_job_and_its_whole_group(harness: Harness) -> None:
    job_id, job_dir = harness.submit("job", ["sh", "-c", "echo $$ > worker.pid; exec sleep 30"])
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
    pid = harness.pid(job_id)
    worker_pid = _pid_from(job_dir / "worker.pid")
    harness.worker_groups.append(worker_pid)

    result = harness.scheduler(["true"]).cancel(job_id)

    assert result.cancelled is True
    assert _final(harness, job_id).state == "CANCELLED"
    _poll(lambda: _group_gone(pid), "the supervisor's process group to disappear")
    _poll(lambda: _group_gone(worker_pid), "the worker's process group to disappear")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc/self/fd")
def test_the_worker_does_not_inherit_the_liveness_lock(harness: Harness) -> None:
    """Proves the property end to end. The `set_inheritable` line itself is pinned by
    `test_the_supervisor_makes_its_lock_descriptor_non_inheritable`."""
    job_id, job_dir = harness.submit("job", ["sh", "-c", "ls -l /proc/self/fd"])

    assert _final(harness, job_id).state == "COMPLETED"
    listing = (job_dir / "slurm_output.log").read_text()
    assert "->" in listing
    assert str(harness.jobs / f"{job_id}.lock") not in listing


def test_a_malformed_job_spec_fails_the_job(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    (tmp_path / "local_job.json").write_text(json.dumps({"argv": [], "hosts": [], "exclusive": 1}))
    lock_fd = os.open(tmp_path / "some.lock", os.O_RDWR | os.O_CREAT)
    try:
        code = supervisor.main([str(tmp_path), "--lock-fd", str(lock_fd)])
    finally:
        os.close(lock_fd)

    assert code == 1
    assert local_state.read_job_state(tmp_path) == "FAILED"
    assert "argv must be a non-empty list" in capsys.readouterr().err


def test_a_missing_job_spec_fails_the_job(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    lock_fd = os.open(tmp_path / "some.lock", os.O_RDWR | os.O_CREAT)
    try:
        code = supervisor.main([str(tmp_path), "--lock-fd", str(lock_fd)])
    finally:
        os.close(lock_fd)

    assert code == 1
    assert local_state.read_job_state(tmp_path) == "FAILED"
    assert "local_job.json" in capsys.readouterr().err


def test_a_relative_job_dir_still_runs_the_worker_in_its_own_dir(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supervisor runs the worker with cwd=job_dir, so the dir given to the worker (here a
    path the worker writes to) must be absolute: `rel` under cwd `rel` is `rel/rel`."""
    monkeypatch.chdir(tmp_path)
    job_id, _ = harness.submit(
        "rel", lambda job_dir: ["sh", "-c", f"echo x > {job_dir}/out.txt"], relative=True
    )

    assert _final(harness, job_id).state == "COMPLETED"
    spec = json.loads((tmp_path / "rel" / "local_job.json").read_text())
    assert spec["argv"][-1] == f"echo x > {tmp_path}/rel/out.txt"
    assert (tmp_path / "rel" / "out.txt").read_text() == "x\n"


def _write_spec(job_dir: Path, argv: list[str]) -> None:
    spec = {"argv": argv, "hosts": [], "exclusive": False, "state_root": str(job_dir / "state")}
    (job_dir / "local_job.json").write_text(json.dumps(spec))


def test_the_supervisor_makes_its_lock_descriptor_non_inheritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, ["true"])
    lock_fd = os.open(tmp_path / "some.lock", os.O_RDWR | os.O_CREAT)
    os.set_inheritable(lock_fd, True)  # as `pass_fds` leaves it in the supervisor
    seen: list[bool] = []

    class _Worker:
        returncode = None

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            seen.append(os.get_inheritable(lock_fd))

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _Worker)
    try:
        code = supervisor.main([str(tmp_path), "--lock-fd", str(lock_fd)])
    finally:
        os.close(lock_fd)

    assert code == 0
    assert seen == [False]


def test_a_bad_lock_descriptor_fails_the_job_instead_of_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _write_spec(tmp_path, ["true"])
    closed_fd = os.open(tmp_path / "some.lock", os.O_RDWR | os.O_CREAT)
    os.close(closed_fd)

    code = supervisor.main([str(tmp_path), "--lock-fd", str(closed_fd)])

    assert code == 1
    assert local_state.read_job_state(tmp_path) == "FAILED"
    assert capsys.readouterr().err.startswith("crab local supervisor:")


# --------------------------------------------------------------------------- #
# cancel and teardown
# --------------------------------------------------------------------------- #
def _state_or_none(job_dir: Path) -> str | None:
    try:
        return _state_file(job_dir)["state"]
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def test_cancel_while_pending_ends_cancelled_and_never_runs(harness: Harness) -> None:
    holder, _ = harness.submit("holder", ["sleep", "30"])
    _poll(lambda: harness.status(holder).state == "RUNNING", "the first job to run")
    waiting, waiting_dir = harness.submit("waiting", ["sh", "-c", "echo x > started"])
    _poll(lambda: _state_or_none(waiting_dir) == "PENDING", "the second job to wait")

    result = harness.scheduler(["true"]).cancel(waiting)

    assert result.cancelled is True
    assert _final(harness, waiting) == JobStatus(waiting, "CANCELLED", "local")
    assert not (waiting_dir / "started").exists()
    assert not (waiting_dir / "local_exit_code").exists()
    assert harness.status(holder).state == "RUNNING"
    _poll(lambda: _group_gone(harness.pid(waiting)), "the cancelled supervisor to exit")


def test_the_worker_runs_in_its_own_process_group(harness: Harness) -> None:
    job_id, job_dir = harness.submit("job", ["sh", "-c", "echo $$ > worker.pid; exec sleep 30"])
    worker_pid = _pid_from(job_dir / "worker.pid")
    harness.worker_groups.append(worker_pid)
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")

    supervisor_pid = harness.pid(job_id)

    assert os.getpgid(supervisor_pid) == supervisor_pid
    assert os.getpgid(worker_pid) == worker_pid != supervisor_pid
    assert _live_members(supervisor_pid) == [supervisor_pid]


IGNORING_WORKER = (
    "import os, signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "open('worker.pid', 'w').write(str(os.getpid()))\n"
    "time.sleep(60)\n"
)


def test_a_worker_that_ignores_sigterm_is_killed_after_the_deadline(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CRAB_CANCEL_DEADLINE` (seconds, read only by the supervisor) shortens the wait between the
    forwarded SIGTERM and the SIGKILL of the worker's group; the default is too long for a test."""
    monkeypatch.setenv("CRAB_CANCEL_DEADLINE", "1.5")
    job_id, job_dir = harness.submit("job", [sys.executable, "-c", IGNORING_WORKER])
    worker_pid = _pid_from(job_dir / "worker.pid")
    harness.worker_groups.append(worker_pid)
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
    time.sleep(0.3)  # the pid file is written after SIG_IGN is installed

    started = time.monotonic()
    assert harness.scheduler(["true"]).cancel(job_id).cancelled is True
    time.sleep(0.8)
    assert _live_members(worker_pid), "the worker must survive SIGTERM until the deadline"
    final = _final(harness, job_id)

    assert final == JobStatus(job_id, "CANCELLED", "local", "-9")
    assert time.monotonic() - started >= 1.4
    assert _group_gone(worker_pid)
    _poll(lambda: _group_gone(harness.pid(job_id)), "the supervisor to exit")
    errors = (job_dir / "slurm_error.log").read_text()
    assert "crab local supervisor: the worker did not stop within 1.5s" in errors
    assert "its apps may still be running" in errors


# A "worker" that watches the supervisor pipe the way `execute_worker` does, and records that its
# SIGTERM handler ran (the real handler unwinds into the app teardown; see test_worker_sigterm.py).
WATCHING_WORKER = """\
import os, signal, sys, time
from crab.cli.orchestrator import _watch_supervisor
from crab.log import get_logger

def on_term(signum, frame):
    open('terminated', 'w').close()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(143)

signal.signal(signal.SIGTERM, on_term)
_watch_supervisor(get_logger())
open('worker.pid', 'w').write(str(os.getpid()))
time.sleep(60)
"""


def test_a_worker_stops_by_itself_when_its_supervisor_dies(harness: Harness) -> None:
    job_id, job_dir = harness.submit("job", [sys.executable, "-c", WATCHING_WORKER])
    worker_pid = _pid_from(job_dir / "worker.pid")
    harness.worker_groups.append(worker_pid)
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
    supervisor_pid = harness.pid(job_id)
    assert _live_members(worker_pid)

    os.kill(supervisor_pid, signal.SIGKILL)  # the supervisor only, not its group

    _poll(lambda: (job_dir / "terminated").exists(), "the worker to notice and stop", timeout=10)
    _poll(lambda: _group_gone(worker_pid), "the worker's group to disappear", timeout=10)
    assert harness.status(job_id) == JobStatus(job_id, "LOST", "local")
    logs = (job_dir / "slurm_output.log").read_text() + (job_dir / "slurm_error.log").read_text()
    assert "Supervisor is gone" in logs


def test_importing_the_supervisor_does_not_import_the_process_manager() -> None:
    """The SIGTERM handler is installed in `main`; a heavy import before it would widen the
    window in which a cancel kills the supervisor with the default action (the job reads LOST)."""
    code = (
        "import sys\n"
        "import crab.core.execution.scheduler.supervisor\n"
        "heavy = sorted(m for m in sys.modules if m.startswith('crab.core.process'))\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_final_state_of_a_cancel_is_written_before_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the exit code first, status would show FAILED (143) for a moment."""
    writes: list[str] = []
    monkeypatch.setattr(local_state, "write_job_state", lambda d, st: writes.append(f"state:{st}"))
    monkeypatch.setattr(supervisor, "_write_exit_code", lambda d, c: writes.append(f"exit:{c}"))
    cancelled = supervisor._Run()
    cancelled.forwarded_at = 1.0

    supervisor._finish(tmp_path, cancelled, 143)
    supervisor._finish(tmp_path, supervisor._Run(), 3)

    assert writes == ["state:CANCELLED", "exit:143", "exit:3", "state:FAILED"]


def test_a_sigterm_after_the_worker_exited_does_not_turn_its_result_into_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, ["true"])
    lock_fd = os.open(tmp_path / "some.lock", os.O_RDWR | os.O_CREAT)

    class _Worker:
        pid = 0
        returncode: int | None = None

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0  # the worker has exited ...
            signal.raise_signal(signal.SIGTERM)  # ... and a cancel lands before `_finish`
            return 0

    monkeypatch.setattr(subprocess, "Popen", _Worker)
    try:
        code = supervisor.main([str(tmp_path), "--lock-fd", str(lock_fd)])
    finally:
        os.close(lock_fd)

    assert code == 0
    assert local_state.read_job_state(tmp_path) == "COMPLETED"
    assert (tmp_path / "local_exit_code").read_text() == "0"
