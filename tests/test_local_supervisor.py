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
        for pid in self.supervisors:
            try:
                _killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in self.supervisors:
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
    job_id, _ = harness.submit("job", ["sleep", "30"])
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
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
    job_id, _ = harness.submit("job", ["sleep", "30"])
    _poll(lambda: harness.status(job_id).state == "RUNNING", "the job to run")
    pid = harness.pid(job_id)

    result = harness.scheduler(["true"]).cancel(job_id)

    assert result.cancelled is True
    _poll(lambda: harness.status(job_id).state != "RUNNING", "the job to stop running")
    _poll(lambda: _group_gone(pid), "the job's process group to disappear")


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
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            seen.append(os.get_inheritable(lock_fd))

        def wait(self) -> int:
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
