"""Tests for `LocalScheduler` (the detached local job path) and `scheduler_for` (the selector).

The local path is dev/testing-only: a detached `bash -c '<worker>; echo $? > local_exit_code'`
process, a state file under `<crab_root>/.crab_local_jobs/`, and status from the exit-code file
or PID liveness.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution.scheduler.base import CancelResult, JobStatus
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.select import scheduler_for
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.log import CrabLogger


def _scheduler(tmp_path: Path, logger: Any = None) -> LocalScheduler:
    return LocalScheduler(logger or MagicMock(), str(tmp_path / "root"))


def _write_state(root: Path, job_id: str, state: dict[str, Any]) -> None:
    jobs = root / ".crab_local_jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / f"{job_id}.json").write_text(json.dumps(state))


# --------------------------------------------------------------------------- #
# selector
# --------------------------------------------------------------------------- #
def test_selector_local_value_gives_local_scheduler() -> None:
    logger = CrabLogger()
    sched = scheduler_for({"CRAB_SCHEDULER": "local"}, logger, "/some/root")
    assert isinstance(sched, LocalScheduler)
    assert sched.log is logger
    assert sched.crab_root == "/some/root"


@pytest.mark.parametrize("environ", [{}, {"CRAB_SCHEDULER": "slurm"}, {"CRAB_SCHEDULER": ""}])
def test_selector_anything_else_gives_slurm_scheduler(environ: dict[str, str]) -> None:
    logger = CrabLogger()
    sched = scheduler_for(environ, logger, "/some/root")
    assert isinstance(sched, SlurmScheduler)
    assert sched.log is logger
    assert sched.crab_root == "/some/root"


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

    assert job_id == "4321"
    exit_file = shlex.quote(str(job_dir / "local_exit_code"))
    worker = f"/opt/py/bin/python /opt/crab/bin/crab worker --workdir {shlex.quote(str(job_dir))}"
    assert fake.argv == ["bash", "-c", f"{worker}; echo $? > {exit_file}"]
    assert fake.kwargs["start_new_session"] is True
    stdout, stderr = fake.kwargs["stdout"], fake.kwargs["stderr"]
    assert stdout.name == str(job_dir / "slurm_output.log")
    assert stderr.name == str(job_dir / "slurm_error.log")
    assert stdout.closed and stderr.closed

    state_path = tmp_path / "root" / ".crab_local_jobs" / "4321.json"
    state = json.loads(state_path.read_text())
    assert state["pid"] == 4321
    assert state["data_dir"] == str(job_dir)
    logger.info.assert_called_with("Submitted local job 4321 (pid 4321)")


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def test_status_exit_file_zero_is_completed(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("0\n")
    _write_state(tmp_path / "root", "7", {"pid": 99999999, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "COMPLETED", "local", "0")]


def test_status_exit_file_nonzero_is_failed(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("1\n")
    _write_state(tmp_path / "root", "7", {"pid": 99999999, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "FAILED", "local", "1")]


def test_status_live_pid_is_running(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    proc = subprocess.Popen(["sleep", "5"])
    try:
        _write_state(tmp_path / "root", "7", {"pid": proc.pid, "data_dir": str(data_dir)})

        assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "RUNNING", "local")]
    finally:
        proc.terminate()
        proc.wait()


def test_status_dead_pid_without_exit_file_is_unknown(tmp_path: Path) -> None:
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    proc = subprocess.Popen(["true"])
    proc.wait()
    _write_state(tmp_path / "root", "7", {"pid": proc.pid, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "UNKNOWN", "local")]


def test_status_keeps_input_order(tmp_path: Path) -> None:
    for jid, code in (("1", "0"), ("2", "1")):
        data_dir = tmp_path / f"job{jid}"
        data_dir.mkdir()
        (data_dir / "local_exit_code").write_text(code)
        _write_state(tmp_path / "root", jid, {"pid": 99999999, "data_dir": str(data_dir)})

    states = _scheduler(tmp_path).status(["2", "1"])

    assert [s.job_id for s in states] == ["2", "1"]


# --------------------------------------------------------------------------- #
# cancel
# --------------------------------------------------------------------------- #
def test_cancel_terminates_the_process_group(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        _write_state(tmp_path / "root", "8", {"pid": proc.pid, "data_dir": str(tmp_path)})

        result = _scheduler(tmp_path).cancel("8")

        assert result == CancelResult(True, None)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cancel_dead_pid_reports_already_finished(tmp_path: Path) -> None:
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    _write_state(tmp_path / "root", "8", {"pid": proc.pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("8")

    assert result == CancelResult(False, "local job already finished.")


def test_cancel_state_without_int_pid_reports_already_finished(tmp_path: Path) -> None:
    _write_state(tmp_path / "root", "8", {"data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("8")

    assert result == CancelResult(False, "local job already finished.")


# --------------------------------------------------------------------------- #
# pids that must never be signalled
# --------------------------------------------------------------------------- #
# killpg(1) is kill(-1) (every process of the user), 0 is the caller's own group, and a bool is an
# int in Python. These tests never reach the real os.killpg / os.kill: both are replaced by a
# recorder, and the tests assert it was never called.
_NO_PID_MESSAGE = "local job state has no valid pid; nothing was signalled."


def _bad_pids() -> list[Any]:
    return [True, False, 0, 1, -1, -12345, "4242", 4242.0, None, os.getpgrp()]


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
    _write_state(tmp_path / "root", "8", {"pid": pid, "data_dir": str(tmp_path)})

    result = _scheduler(tmp_path).cancel("8")

    assert result == CancelResult(False, _NO_PID_MESSAGE)
    assert calls == []


@pytest.mark.parametrize("pid", _bad_pids(), ids=repr)
def test_status_never_probes_an_invalid_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    _write_state(tmp_path / "root", "7", {"pid": pid, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "UNKNOWN", "local")]
    assert calls == []


def test_status_exit_file_wins_over_an_invalid_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    data_dir = tmp_path / "job"
    data_dir.mkdir()
    (data_dir / "local_exit_code").write_text("0\n")
    _write_state(tmp_path / "root", "7", {"pid": True, "data_dir": str(data_dir)})

    assert _scheduler(tmp_path).status(["7"]) == [JobStatus("7", "COMPLETED", "local", "0")]
    assert calls == []


def test_cancel_missing_state_file_reports_already_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)

    assert _scheduler(tmp_path).cancel("8") == CancelResult(False, "local job already finished.")
    assert calls == []


def test_cancel_valid_pid_that_is_gone_reports_already_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", gone)
    _write_state(tmp_path / "root", "8", {"pid": 424242, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("8") == CancelResult(False, "local job already finished.")


def test_cancel_valid_pid_signals_its_group_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_signals(monkeypatch)
    _write_state(tmp_path / "root", "8", {"pid": 424242, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("8") == CancelResult(True, None)
    assert calls == [("killpg", 424242, signal.SIGTERM)]


# --------------------------------------------------------------------------- #
# knows, node_list, describe_nodes
# --------------------------------------------------------------------------- #
def test_knows_is_true_only_for_a_readable_state_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write_state(root, "1", {"pid": 1, "data_dir": "x"})
    (root / ".crab_local_jobs" / "2.json").write_text("{not json")
    sched = _scheduler(tmp_path)

    assert sched.knows("1") is True
    assert sched.knows("2") is False
    assert sched.knows("3") is False


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


def test_cancel_kills_through_the_os_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing suites patch `os.killpg` globally; the scheduler must go through the module."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    _write_state(tmp_path / "root", "8", {"pid": 31337, "data_dir": str(tmp_path)})

    assert _scheduler(tmp_path).cancel("8") == CancelResult(True, None)
    assert calls and calls[0][0] == 31337
