"""Tests for `SlurmScheduler.node_list`: the worker's node list, read from scontrol in memory."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution.scheduler.slurm import SlurmScheduler


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
    monkeypatch.setenv("SLURM_NODELIST", "lrdn[0001-0002]")
    fake = _fake(monkeypatch, stdout="lrdn0001\nlrdn0002\n")
    assert _scheduler(tmp_path).node_list() == ["lrdn0001", "lrdn0002"]
    assert [cmd for cmd, _ in fake.calls] == [["scontrol", "show", "hostnames", "lrdn[0001-0002]"]]


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
