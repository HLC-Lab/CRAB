"""Tests for `SlurmScheduler`: `node_list` (scontrol), `status` (squeue, sacct), `cancel` (scancel)
and `describe_nodes` (sinfo), all against fake command runners."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.cli import contract
from crab.core.execution.scheduler.base import CancelResult, JobStatus
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.log import CrabLogger


class _FakeScontrol:
    """Stands in for `subprocess.run`; records every call."""

    def __init__(self, stdout: str = "", error: BaseException | None = None) -> None:
        self.stdout = stdout
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> MagicMock:
        self.calls.append((list(cmd), kwargs))
        if self.error is not None and kwargs.get("check"):
            raise self.error
        return MagicMock(returncode=0, stdout=self.stdout)


def _scheduler(tmp_path: Path) -> SlurmScheduler:
    return SlurmScheduler(MagicMock(), crab_root=str(tmp_path))


def _fake(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _FakeScontrol:
    fake = _FakeScontrol(**kwargs)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_node_list_expands_the_allocation(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NODELIST", "node[0001-0002]")
    fake = _fake(monkeypatch, stdout="node0001\nnode0002\n")
    assert _scheduler(tmp_path).node_list() == ["node0001", "node0002"]
    assert [cmd for cmd, _ in fake.calls] == [["scontrol", "show", "hostnames", "node[0001-0002]"]]


def test_numeric_looking_hostnames_stay_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NODELIST", "[0001-0002]")
    _fake(monkeypatch, stdout="0001\n0002\n")
    assert _scheduler(tmp_path).node_list() == ["0001", "0002"]


def test_whitespace_and_blank_lines_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NODELIST", "n[1-2]")
    _fake(monkeypatch, stdout="  n1 \n\n n2\n")
    assert _scheduler(tmp_path).node_list() == ["n1", "n2"]


def test_scontrol_stderr_is_not_captured(tmp_path, monkeypatch):
    """scontrol's own error text must reach the worker's stderr, so it is not captured."""
    monkeypatch.setenv("SLURM_NODELIST", "node01")
    fake = _fake(monkeypatch, stdout="node01\n")
    _scheduler(tmp_path).node_list()
    kwargs = fake.calls[0][1]
    assert "capture_output" not in kwargs
    assert "stderr" not in kwargs
    assert kwargs["check"] is True


def test_unset_nodelist_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_NODELIST", raising=False)
    fake = _fake(monkeypatch, stdout="n1\n")
    with pytest.raises(RuntimeError, match="SLURM_NODELIST"):
        _scheduler(tmp_path).node_list()
    assert fake.calls == []


def test_scontrol_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NODELIST", "node01")
    _fake(monkeypatch, error=subprocess.CalledProcessError(1, "scontrol"))
    with pytest.raises(subprocess.CalledProcessError):
        _scheduler(tmp_path).node_list()


def test_empty_scontrol_output_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NODELIST", "node01")
    _fake(monkeypatch, stdout="\n")
    with pytest.raises(RuntimeError) as excinfo:
        _scheduler(tmp_path).node_list()
    assert "scontrol show hostnames" in str(excinfo.value)
    assert "node01" in str(excinfo.value)


def test_node_list_creates_no_file(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SLURM_NODELIST", "node01")
    _fake(monkeypatch, stdout="node01\n")
    before = (sorted(job_dir.iterdir()), sorted(cwd.iterdir()))
    assert _scheduler(tmp_path).node_list() == ["node01"]
    assert (sorted(job_dir.iterdir()), sorted(cwd.iterdir())) == before == ([], [])


class _FakeRunner:
    """Stands in for the scheduler's command runner: answers by program name, records argv."""

    def __init__(self, answers: dict[str, str | BaseException]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> str:
        self.calls.append(list(cmd))
        answer = self.answers.get(cmd[0], FileNotFoundError(cmd[0]))
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _with_runner(runner: Callable[[list[str]], str]) -> SlurmScheduler:
    return SlurmScheduler(CrabLogger(), "/", runner=runner)


def test_status_reads_squeue_then_sacct_in_input_order():
    runner = _FakeRunner(
        {"squeue": "1|RUNNING\n", "sacct": "2|COMPLETED|0:0\n2.batch|COMPLETED|0:0\n"}
    )
    assert _with_runner(runner).status(["1", "2"]) == [
        JobStatus("1", "RUNNING", "squeue"),
        JobStatus("2", "COMPLETED", "sacct", "0:0"),
    ]


def test_status_of_no_ids_runs_no_command():
    runner = _FakeRunner({})
    assert _with_runner(runner).status([]) == []
    assert runner.calls == []


def test_cancel_reports_success():
    runner = _FakeRunner({"scancel": ""})
    assert _with_runner(runner).cancel("7") == CancelResult(True, None)
    assert runner.calls == [["scancel", "7"]]


def test_cancel_without_scancel_reports_it():
    runner = _FakeRunner({"scancel": FileNotFoundError("scancel")})
    assert _with_runner(runner).cancel("7") == CancelResult(
        False, "scancel is not available on this host."
    )


def test_cancel_of_a_gone_job_reports_the_exit_code():
    runner = _FakeRunner({"scancel": subprocess.CalledProcessError(1, "scancel")})
    assert _with_runner(runner).cancel("7") == CancelResult(
        False, "scancel exited 1; the job may already be gone."
    )


def test_describe_nodes_has_no_schema_key():
    runner = _FakeRunner({"sinfo": "gpu|up|2\n"})
    described = _with_runner(runner).describe_nodes()
    assert list(described) == ["available", "partitions", "nodes"]
    assert described["available"] is True
    assert described["partitions"] == [{"name": "gpu", "avail": "up", "nodes": 2}]


def test_describe_nodes_without_sinfo_carries_a_note():
    runner = _FakeRunner({})
    assert _with_runner(runner).describe_nodes() == {
        "available": False,
        "partitions": [],
        "nodes": [],
        "note": "sinfo unavailable: FileNotFoundError",
    }


def test_default_runner_calls_check_output_with_stderr_discarded(monkeypatch):
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_check_output(cmd: list[str], **kwargs: Any) -> str:
        calls.append((list(cmd), kwargs))
        return ""

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    SlurmScheduler(CrabLogger(), "/").cancel("7")
    assert calls == [(["scancel", "7"], {"text": True, "stderr": subprocess.DEVNULL})]


class _ArgvRunner:
    """A fake runner that answers by the exact argv, so each call can be told apart."""

    def __init__(self, answers: dict[tuple[str, ...], str | BaseException]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> str:
        self.calls.append(list(cmd))
        answer = self.answers.get(tuple(cmd), FileNotFoundError(cmd[0]))
        if isinstance(answer, BaseException):
            raise answer
        return answer


_SQUEUE_1 = ("squeue", "-h", "-o", "%i|%T", "-j", "1")
_SACCT_2 = ("sacct", "-j", "2", "-n", "-P", "-o", "JobID,State,ExitCode")
_SINFO_PARTITIONS = ("sinfo", "-h", "-o", "%R|%a|%D")
_SINFO_NODES = ("sinfo", "-h", "-o", "%N")


@pytest.mark.parametrize(
    "error", [subprocess.CalledProcessError(1, "sacct"), FileNotFoundError("sacct")]
)
def test_status_when_sacct_fails_is_unknown_with_source_none(error):
    runner = _FakeRunner({"sacct": error})
    assert _with_runner(runner).status(["2"]) == [JobStatus("2", "UNKNOWN", "none")]


def test_status_with_a_short_sacct_row_has_no_exit_code():
    runner = _FakeRunner({"sacct": "2|FAILED\n"})
    [status] = _with_runner(runner).status(["2"])
    assert status == JobStatus("2", "FAILED", "sacct", None)
    assert status.exit_code is None


def test_contract_status_omits_exit_code_for_a_short_sacct_row(tmp_path):
    runner = _FakeRunner({"sacct": "2|FAILED\n"})
    result = contract.gather_status(["2"], runner=runner, crab_root=tmp_path)
    assert result["jobs"] == [{"job_id": "2", "state": "FAILED", "source": "sacct"}]
    assert "exit_code" not in result["jobs"][0]


def test_status_keeps_input_order():
    runner = _FakeRunner({"squeue": "1|RUNNING\n", "sacct": "2|COMPLETED|0:0\n"})
    assert _with_runner(runner).status(["2", "1"]) == [
        JobStatus("2", "COMPLETED", "sacct", "0:0"),
        JobStatus("1", "RUNNING", "squeue"),
    ]


def test_status_falls_back_to_sacct_for_every_id_when_squeue_fails():
    sacct_1 = ("sacct", "-j", "1", "-n", "-P", "-o", "JobID,State,ExitCode")
    runner = _ArgvRunner(
        {
            ("squeue", "-h", "-o", "%i|%T", "-j", "1,2"): subprocess.CalledProcessError(
                1, "squeue"
            ),
            sacct_1: "1|COMPLETED|0:0\n",
            _SACCT_2: "2|FAILED|1:0\n",
        }
    )
    assert _with_runner(runner).status(["1", "2"]) == [
        JobStatus("1", "COMPLETED", "sacct", "0:0"),
        JobStatus("2", "FAILED", "sacct", "1:0"),
    ]
    assert runner.calls == [
        ["squeue", "-h", "-o", "%i|%T", "-j", "1,2"],
        list(sacct_1),
        list(_SACCT_2),
    ]


def test_describe_nodes_expands_the_node_tokens():
    runner = _ArgvRunner({_SINFO_PARTITIONS: "gpu|up|2\n", _SINFO_NODES: "n[01-02,07]\n"})
    described = _with_runner(runner).describe_nodes()
    assert described["nodes"] == ["n[01-02]", "n[07]"]
    assert runner.calls == [list(_SINFO_PARTITIONS), list(_SINFO_NODES)]


def test_describe_nodes_when_sinfo_fails_notes_the_error_type():
    runner = _ArgvRunner({_SINFO_PARTITIONS: subprocess.CalledProcessError(1, "sinfo")})
    assert _with_runner(runner).describe_nodes() == {
        "available": False,
        "partitions": [],
        "nodes": [],
        "note": "sinfo unavailable: CalledProcessError",
    }


def test_describe_nodes_keeps_partitions_when_the_node_query_fails():
    runner = _ArgvRunner(
        {
            _SINFO_PARTITIONS: "gpu|up|2\n",
            _SINFO_NODES: subprocess.CalledProcessError(1, "sinfo"),
        }
    )
    described = _with_runner(runner).describe_nodes()
    assert described["available"] is True
    assert described["partitions"] == [{"name": "gpu", "avail": "up", "nodes": 2}]
    assert described["nodes"] == []
