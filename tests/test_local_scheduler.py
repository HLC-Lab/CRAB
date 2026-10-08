"""Tests for `LocalScheduler` (the detached local job path) and `scheduler_for` (the selector).

The local path is dev/testing-only: a detached supervisor (`scheduler/supervisor.py`, spawned here
through a faked `Popen`; tests/test_local_supervisor.py runs real ones), a job record under
`$XDG_STATE_HOME/crab/jobs/` (a per-test directory, see conftest), and a status derived from the
liveness lock, `local_state.json` and the exit-code file. Liveness is tested with real lock files,
never with processes.
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import crab
from crab.core.execution.scheduler import local_state
from crab.core.execution.scheduler.base import CancelResult, JobStatus
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.local_state import (
    jobs_dir,
    open_liveness_lock,
    write_job_state,
)
from crab.core.execution.scheduler.select import scheduler_for
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.core.execution.settings import from_preset, to_json
from crab.log import CrabLogger


def _environ(tmp_path: Path) -> dict[str, str]:
    return {"XDG_STATE_HOME": str(tmp_path / "xdg-state")}


def _scheduler(tmp_path: Path, logger: Any = None) -> LocalScheduler:
    return LocalScheduler(logger or MagicMock(), str(tmp_path / "root"), environ=_environ(tmp_path))


def _write_state(tmp_path: Path, job_id: str, fields: dict[str, Any]) -> None:
    """Write a job record file as `write_record` would, with `fields` overriding the defaults."""
    jobs = jobs_dir(_environ(tmp_path))
    jobs.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "id": job_id,
        "host": socket.gethostname(),
        "data_dir": str(tmp_path),
        "supervisor_pid": 424242,
        "created": "2026-10-08T10:00:00",
    }
    record.update(fields)
    (jobs / f"{job_id}.json").write_text(json.dumps(record))


@pytest.fixture
def hold_lock(tmp_path: Path) -> Iterator[Callable[[str], None]]:
    """A function that takes a job's liveness lock (the job is then alive); released at teardown."""
    fds: list[int] = []

    def hold(job_id: str) -> None:
        fds.append(open_liveness_lock(jobs_dir(_environ(tmp_path)), job_id))

    try:
        yield hold
    finally:
        for fd in fds:
            os.close(fd)


# --------------------------------------------------------------------------- #
# selector
# --------------------------------------------------------------------------- #
def test_selector_local_gives_local_scheduler() -> None:
    logger = CrabLogger()
    sched = scheduler_for("local", logger, "/some/root")
    assert isinstance(sched, LocalScheduler)
    assert sched.log is logger
    assert sched.crab_root == "/some/root"


def test_selector_slurm_gives_slurm_scheduler() -> None:
    logger = CrabLogger()
    sched = scheduler_for("slurm", logger, "/some/root")
    assert isinstance(sched, SlurmScheduler)
    assert sched.log is logger
    assert sched.crab_root == "/some/root"


@pytest.mark.parametrize("value", ["", "pbs"])
def test_selector_refuses_any_other_value(value: str) -> None:
    with pytest.raises(ValueError, match="scheduler"):
        scheduler_for(value, CrabLogger(), "/some/root")


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #
class _FakePopen:
    """Stands in for `subprocess.Popen`; records argv and kwargs, and whether the job's liveness
    lock was held at the moment of the call (the supervisor must get a lock that is already taken)."""

    def __init__(self, tmp_path: Path, pid: int = 4321) -> None:
        self.tmp_path = tmp_path
        self.pid = pid
        self.argv: list[str] | None = None
        self.kwargs: dict[str, Any] = {}
        self.lock_held: bool | None = None
        self.waited: dict[str, Any] | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakePopen:
        self.argv = argv
        self.kwargs = kwargs
        self.lock_held = local_state.is_alive(jobs_dir(_environ(self.tmp_path)), "local-1")
        return self

    def wait(self, **kwargs: Any) -> int:
        self.waited = kwargs
        return 0


def _job_dir(tmp_path: Path, name: str = "data dir") -> Path:
    job_dir = tmp_path / name
    job_dir.mkdir()
    return job_dir


def test_submit_spawns_a_detached_supervisor_and_records_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = _job_dir(tmp_path)
    fake = _FakePopen(tmp_path, pid=4321)
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python")
    monkeypatch.setattr(sys, "argv", ["/opt/crab/bin/crab", "run"])
    logger = MagicMock()
    sched = _scheduler(tmp_path, logger)

    job_id = sched.submit(str(job_dir), {})

    assert job_id == "local-1"
    assert fake.argv is not None
    fd = int(fake.argv[-1])
    assert fake.argv == [
        "/opt/py/bin/python",
        "-m",
        "crab.core.execution.scheduler.supervisor",
        str(job_dir),
        "--lock-fd",
        str(fd),
    ]
    assert fake.kwargs["start_new_session"] is True
    assert fake.kwargs["pass_fds"] == (fd,)
    assert fake.kwargs["stdin"] == subprocess.DEVNULL
    stdout, stderr = fake.kwargs["stdout"], fake.kwargs["stderr"]
    assert stdout.name == str(job_dir / "slurm_output.log")
    assert stderr.name == str(job_dir / "slurm_error.log")
    assert stdout.closed and stderr.closed
    # The lock was held when the supervisor started, and the parent let go of its copy afterwards.
    assert fake.lock_held is True
    assert not local_state.is_alive(jobs_dir(_environ(tmp_path)), "local-1")
    with pytest.raises(OSError):
        os.fstat(fd)

    spec = json.loads((job_dir / "local_job.json").read_text())
    assert spec == {
        "id": "local-1",
        "argv": [
            "/opt/py/bin/python",
            "/opt/crab/bin/crab",
            "worker",
            "--workdir",
            str(job_dir),
        ],
        "hosts": ["localhost"],
        "exclusive": True,
        "state_root": str(tmp_path / "xdg-state" / "crab"),
    }
    record = json.loads((jobs_dir(_environ(tmp_path)) / "local-1.json").read_text())
    assert record["id"] == "local-1"
    assert record["host"] == socket.gethostname()
    assert record["data_dir"] == str(job_dir)
    assert record["supervisor_pid"] == 4321
    assert datetime.datetime.fromisoformat(record["created"])
    assert not (tmp_path / "root" / ".crab_local_jobs").exists()
    logger.info.assert_called_with("Submitted local job local-1 (pid 4321)")


def test_the_supervisor_runs_this_same_crab_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = str(Path(crab.__file__).resolve().parents[1])
    fake = _FakePopen(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    _scheduler(tmp_path).submit(str(_job_dir(tmp_path, "a")), {})
    assert fake.kwargs["env"]["PYTHONPATH"] == root

    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    monkeypatch.setattr(subprocess, "Popen", fake := _FakePopen(tmp_path))
    _scheduler(tmp_path).submit(str(_job_dir(tmp_path, "b")), {})
    assert fake.kwargs["env"]["PYTHONPATH"] == f"{root}{os.pathsep}/somewhere/else"


def test_submit_locks_the_first_numnodes_hosts_of_execution_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = _job_dir(tmp_path)
    preset = {"scheduler": "local", "exclusive": False, "hosts": ["a:4", "b:4", "c:4"]}
    (job_dir / "execution.json").write_text(json.dumps(to_json(from_preset(preset, "t"))))
    monkeypatch.setattr(subprocess, "Popen", _FakePopen(tmp_path))

    _scheduler(tmp_path).submit(str(job_dir), {"numnodes": 2})

    spec = json.loads((job_dir / "local_job.json").read_text())
    assert spec["hosts"] == ["a", "b"]
    assert spec["exclusive"] is False


def test_a_failing_record_write_signals_the_supervisor_group_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = _job_dir(tmp_path)
    fake = _FakePopen(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", fake)

    def disk_full(jobs: Path, record: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(local_state, "write_record", disk_full)
    calls = _forbid_signals(monkeypatch)  # the fake pid is never really signalled

    with pytest.raises(ValueError, match="disk full") as info:
        _scheduler(tmp_path).submit(str(job_dir), {})

    assert str(jobs_dir(_environ(tmp_path))) in str(info.value)
    assert calls == [("killpg", 4321, signal.SIGTERM)]
    assert fake.waited == {"timeout": 5}


def test_submit_gives_each_job_its_own_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_dir = _job_dir(tmp_path, "data")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen(tmp_path))
    sched = _scheduler(tmp_path)

    assert [sched.submit(str(job_dir), {}), sched.submit(str(job_dir), {})] == [
        "local-1",
        "local-2",
    ]
    assert sorted(p.name for p in jobs_dir(_environ(tmp_path)).glob("local-*.json")) == [
        "local-1.json",
        "local-2.json",
    ]


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def test_status_exit_file_zero_is_completed(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("0\n")
    _write_state(tmp_path, "local-7", {"supervisor_pid": 99999999, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [
        JobStatus("local-7", "COMPLETED", "local", "0")
    ]


def test_status_exit_file_nonzero_is_failed(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("1\n")
    _write_state(tmp_path, "local-7", {"supervisor_pid": 99999999, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [
        JobStatus("local-7", "FAILED", "local", "1")
    ]


_FINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT")


@pytest.mark.parametrize("exit_file", [None, "0", "2"], ids=["no-exit-file", "exit-0", "exit-2"])
@pytest.mark.parametrize(
    "state", [None, "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"], ids=str
)
@pytest.mark.parametrize("alive", [True, False], ids=["alive", "dead"])
def test_status_is_derived_from_the_lock_the_state_file_and_the_exit_file(
    tmp_path: Path,
    hold_lock: Callable[[str], None],
    alive: bool,
    state: str | None,
    exit_file: str | None,
) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-7", {"data_dir": str(data_dir)})
    if alive:
        hold_lock("local-7")
    if state is not None:
        write_job_state(data_dir, state)
    if exit_file is not None:
        (data_dir / "local_exit_code").write_text(exit_file + "\n")

    expected: JobStatus
    if exit_file is not None:
        expected = JobStatus(
            "local-7", "COMPLETED" if exit_file == "0" else "FAILED", "local", exit_file
        )
    elif alive:
        expected = JobStatus("local-7", state or "PENDING", "local")
    else:
        expected = JobStatus("local-7", state if state in _FINAL else "LOST", "local")

    assert _scheduler(tmp_path).status(["local-7"]) == [expected]


def test_status_of_an_exit_file_needs_no_lock_and_no_state_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("0\n")
    _write_state(tmp_path, "local-7", {"supervisor_pid": 99999999, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [
        JobStatus("local-7", "COMPLETED", "local", "0")
    ]


def test_status_keeps_input_order(tmp_path: Path) -> None:
    for jid, code in (("local-1", "0"), ("local-2", "1")):
        data_dir = tmp_path / f"job{jid}"
        data_dir.mkdir()
        (data_dir / "local_exit_code").write_text(code)
        _write_state(tmp_path, jid, {"supervisor_pid": 99999999, "data_dir": str(data_dir)})

    states = _scheduler(tmp_path).status(["local-2", "local-1"])

    assert [s.job_id for s in states] == ["local-2", "local-1"]


# --------------------------------------------------------------------------- #
# cancel
# --------------------------------------------------------------------------- #
def test_cancel_terminates_the_process_group(
    tmp_path: Path, hold_lock: Callable[[str], None]
) -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        hold_lock("local-8")
        _write_state(tmp_path, "local-8", {"supervisor_pid": proc.pid, "data_dir": str(tmp_path)})

        result = _scheduler(tmp_path).cancel("local-8")

        assert result == CancelResult(True, None)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cancel_dead_pid_reports_already_finished(
    tmp_path: Path, hold_lock: Callable[[str], None]
) -> None:
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    hold_lock("local-8")
    _write_state(tmp_path, "local-8", {"supervisor_pid": proc.pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("local-8")

    assert result == CancelResult(False, "local job already finished.")


def test_cancel_never_signals_a_job_whose_lock_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No lock holder means the supervisor is gone; its pid may belong to someone else now."""
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("local-8")

    assert result == CancelResult(
        False, "local job is not running (its supervisor is gone); nothing was signalled."
    )
    assert calls == []


def test_cancel_record_without_a_pid_is_an_error(tmp_path: Path) -> None:
    jobs_dir(_environ(tmp_path)).mkdir(parents=True)
    (jobs_dir(_environ(tmp_path)) / "local-8.json").write_text(
        json.dumps({"id": "local-8", "host": socket.gethostname(), "data_dir": str(tmp_path)})
    )

    with pytest.raises(ValueError, match=r"local-8\.json"):
        _scheduler(tmp_path).cancel("local-8")


# --------------------------------------------------------------------------- #
# pids that must never be signalled
# --------------------------------------------------------------------------- #
# killpg(1) is kill(-1) (every process of the user), 0 is the caller's own group, and a bool is an
# int in Python. These tests never reach the real os.killpg / os.kill: both are replaced by a
# recorder, and the tests assert it was never called.
_NO_PID_MESSAGE = "local job state has no valid pid; nothing was signalled."


def _bad_pids() -> list[Any]:
    """Ints the record reader accepts and `_signal_target` must refuse."""
    # 2**31 and above overflow the C pid_t (os.kill raises OverflowError); 4194304 is the largest
    # Linux pid_max, so anything above it is no pid.
    return [0, 1, -1, -12345, os.getpgrp(), 4194305, 2**31, 2**70]


def _bad_pid_types() -> list[Any]:
    """Values the record reader refuses outright (a bool is an int in Python)."""
    return [True, False, "4242", 4242.0, None]


def _forbid_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
    return calls


@pytest.mark.parametrize("pid", _bad_pids(), ids=repr)
def test_cancel_never_signals_an_invalid_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None], pid: Any
) -> None:
    """The lock is held, so `_signal_target` is what refuses."""
    calls = _forbid_signals(monkeypatch)
    hold_lock("local-8")
    _write_state(tmp_path, "local-8", {"supervisor_pid": pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("local-8")

    assert result == CancelResult(False, _NO_PID_MESSAGE)
    assert calls == []


@pytest.mark.parametrize("pid", _bad_pids(), ids=repr)
def test_status_never_signals_or_probes_the_recorded_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-7", {"supervisor_pid": pid, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [JobStatus("local-7", "LOST", "local")]
    assert calls == []


@pytest.mark.parametrize("pid", _bad_pid_types(), ids=repr)
def test_a_record_with_a_non_int_pid_is_an_error_and_signals_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path, "local-8", {"supervisor_pid": pid})
    sched = _scheduler(tmp_path)

    with pytest.raises(ValueError, match=r"local-8\.json"):
        sched.cancel("local-8")
    with pytest.raises(ValueError, match=r"local-8\.json"):
        sched.status(["local-8"])
    assert calls == []


def test_status_exit_file_wins_over_an_invalid_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("0\n")
    _write_state(tmp_path, "local-7", {"supervisor_pid": 1, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [
        JobStatus("local-7", "COMPLETED", "local", "0")
    ]
    assert calls == []


def test_cancel_valid_pid_that_is_gone_reports_already_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", gone)
    hold_lock("local-8")
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(
        False, "local job already finished."
    )


def test_the_largest_linux_pid_is_still_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    calls = _forbid_signals(monkeypatch)
    hold_lock("local-8")
    _write_state(tmp_path, "local-8", {"supervisor_pid": 4194304})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls == [("killpg", 4194304, signal.SIGTERM)]


def test_cancel_valid_pid_signals_its_group_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    calls = _forbid_signals(monkeypatch)
    hold_lock("local-8")
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls == [("killpg", 424242, signal.SIGTERM)]


# --------------------------------------------------------------------------- #
# unknown and foreign jobs, node_list, describe_nodes
# --------------------------------------------------------------------------- #
def test_unknown_local_job_is_unknown_and_not_cancellable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    sched = _scheduler(tmp_path)

    assert sched.status(["local-9"]) == [JobStatus("local-9", "UNKNOWN", "local")]
    assert sched.cancel("local-9") == CancelResult(False, "no such local job")
    assert calls == []


def test_a_record_from_another_host_is_an_error_for_status_and_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path, "local-5", {"host": "some-other-host"})
    sched = _scheduler(tmp_path)

    for call in (lambda: sched.status(["local-5"]), lambda: sched.cancel("local-5")):
        with pytest.raises(ValueError) as info:
            call()
        assert "some-other-host" in str(info.value)
        assert socket.gethostname() in str(info.value)
    assert calls == []


def test_a_malformed_job_id_is_an_error(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)

    with pytest.raises(ValueError, match="invalid job id"):
        sched.status(["../1"])
    with pytest.raises(ValueError, match="invalid job id"):
        sched.cancel("bogus!")


def test_node_list_is_localhost_and_runs_no_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("node_list must not run a command")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)

    assert _scheduler(tmp_path).node_list() == ["localhost"]


def test_describe_nodes_reports_localhost(tmp_path: Path) -> None:
    assert _scheduler(tmp_path).describe_nodes() == {
        "available": True,
        "partitions": [],
        "nodes": ["localhost"],
    }


@pytest.mark.parametrize("code", ["0", "1"])
def test_cancel_finished_job_is_never_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """An exit file means the job finished: its pid may already belong to someone else."""
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text(f"{code}\n")
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(
        False, "local job already finished."
    )
    assert calls == []


def test_cancel_without_exit_file_signals_the_group_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    calls = _forbid_signals(monkeypatch)
    hold_lock("local-8")
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls == [("killpg", 424242, signal.SIGTERM)]


def test_own_process_group_is_never_signalled_or_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    """Pins `os.getpgrp`, which is 0 in a PID namespace and would hide the own-group clause."""
    calls = _forbid_signals(monkeypatch)
    hold_lock("local-8")
    monkeypatch.setattr(os, "getpgrp", lambda: 424242)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(data_dir)})
    sched = _scheduler(tmp_path)

    assert sched.cancel("local-8") == CancelResult(False, _NO_PID_MESSAGE)
    assert sched.status(["local-8"]) == [JobStatus("local-8", "PENDING", "local")]
    assert calls == []


def test_cancel_kills_through_the_os_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_lock: Callable[[str], None]
) -> None:
    """Existing suites patch `os.killpg` globally; the scheduler must go through the module."""
    hold_lock("local-8")
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    _write_state(tmp_path, "local-8", {"supervisor_pid": 31337, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls and calls[0][0] == 31337
