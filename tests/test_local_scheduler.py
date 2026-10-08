"""Tests for `LocalScheduler` (the detached local job path) and `scheduler_for` (the selector).

The local path is dev/testing-only: a detached `bash -c '<worker>; echo $? > local_exit_code'`
process, a job record under `$XDG_STATE_HOME/crab/jobs/` (a per-test directory, see conftest), and
status from the exit-code file or PID liveness.
"""

from __future__ import annotations

import datetime
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution.scheduler.base import CancelResult, JobStatus
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.local_state import jobs_dir
from crab.core.execution.scheduler.select import scheduler_for
from crab.core.execution.scheduler.slurm import SlurmScheduler
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
    """Stands in for `subprocess.Popen`; records argv, kwargs and the redirect handles."""

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.argv: list[str] | None = None
        self.kwargs: dict[str, Any] = {}

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakePopen:
        self.argv = argv
        self.kwargs = kwargs
        return self


def test_submit_spawns_detached_bash_and_records_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = tmp_path / "data dir"
    job_dir.mkdir()
    fake = _FakePopen(pid=4321)
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python")
    monkeypatch.setattr(sys, "argv", ["/opt/crab/bin/crab", "run"])
    logger = MagicMock()
    sched = _scheduler(tmp_path, logger)

    job_id = sched.submit(str(job_dir), {})

    assert job_id == "local-1"
    exit_file = shlex.quote(str(job_dir / "local_exit_code"))
    worker = f"/opt/py/bin/python /opt/crab/bin/crab worker --workdir {shlex.quote(str(job_dir))}"
    assert fake.argv == ["bash", "-c", f"{worker}; echo $? > {exit_file}"]
    assert fake.kwargs["start_new_session"] is True
    stdout, stderr = fake.kwargs["stdout"], fake.kwargs["stderr"]
    assert stdout.name == str(job_dir / "slurm_output.log")
    assert stderr.name == str(job_dir / "slurm_error.log")
    assert stdout.closed and stderr.closed

    record = json.loads((jobs_dir(_environ(tmp_path)) / "local-1.json").read_text())
    assert record["id"] == "local-1"
    assert record["host"] == socket.gethostname()
    assert record["data_dir"] == str(job_dir)
    assert record["supervisor_pid"] == 4321
    assert datetime.datetime.fromisoformat(record["created"])
    assert not (tmp_path / "root" / ".crab_local_jobs").exists()
    logger.info.assert_called_with("Submitted local job local-1 (pid 4321)")


def test_submit_gives_each_job_its_own_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_dir = tmp_path / "data"
    job_dir.mkdir()
    monkeypatch.setattr(subprocess, "Popen", _FakePopen(pid=4321))
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


def test_status_live_pid_is_running(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    proc = subprocess.Popen(["sleep", "5"])
    try:
        _write_state(tmp_path, "local-7", {"supervisor_pid": proc.pid, "data_dir": str(data_dir)})

        assert _scheduler(tmp_path).status(["local-7"]) == [
            JobStatus("local-7", "RUNNING", "local")
        ]
    finally:
        proc.terminate()
        proc.wait()


def test_status_dead_pid_without_exit_file_is_unknown(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    proc = subprocess.Popen(["true"])
    proc.wait()
    _write_state(tmp_path, "local-7", {"supervisor_pid": proc.pid, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [JobStatus("local-7", "UNKNOWN", "local")]


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
def test_cancel_terminates_the_process_group(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        _write_state(tmp_path, "local-8", {"supervisor_pid": proc.pid, "data_dir": str(tmp_path)})

        result = _scheduler(tmp_path).cancel("local-8")

        assert result == CancelResult(True, None)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cancel_dead_pid_reports_already_finished(tmp_path: Path) -> None:
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    _write_state(tmp_path, "local-8", {"supervisor_pid": proc.pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("local-8")

    assert result == CancelResult(False, "local job already finished.")


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path, "local-8", {"supervisor_pid": pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("local-8")

    assert result == CancelResult(False, _NO_PID_MESSAGE)
    assert calls == []


@pytest.mark.parametrize("pid", _bad_pids(), ids=repr)
def test_status_never_probes_an_invalid_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-7", {"supervisor_pid": pid, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["local-7"]) == [JobStatus("local-7", "UNKNOWN", "local")]
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", gone)
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(
        False, "local job already finished."
    )


def test_the_largest_linux_pid_is_still_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path, "local-8", {"supervisor_pid": 4194304})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls == [("killpg", 4194304, signal.SIGTERM)]


def test_cancel_valid_pid_signals_its_group_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls == [("killpg", 424242, signal.SIGTERM)]


def test_own_process_group_is_never_signalled_or_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins `os.getpgrp`, which is 0 in a PID namespace and would hide the own-group clause."""
    calls = _forbid_signals(monkeypatch)
    monkeypatch.setattr(os, "getpgrp", lambda: 424242)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path, "local-8", {"supervisor_pid": 424242, "data_dir": str(data_dir)})
    sched = _scheduler(tmp_path)

    assert sched.cancel("local-8") == CancelResult(False, _NO_PID_MESSAGE)
    assert sched.status(["local-8"]) == [JobStatus("local-8", "UNKNOWN", "local")]
    assert calls == []


def test_cancel_kills_through_the_os_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing suites patch `os.killpg` globally; the scheduler must go through the module."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    _write_state(tmp_path, "local-8", {"supervisor_pid": 31337, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("local-8") == CancelResult(True, None)
    assert calls and calls[0][0] == 31337
