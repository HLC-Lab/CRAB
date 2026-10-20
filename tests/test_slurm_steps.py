"""`cancel_leftover_steps` cancels Slurm steps still running after a run's apps were stopped.

squeue, scancel and the settle sleep are all faked: nothing here reaches a real Slurm.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from crab.core.process import slurm_steps
from crab.core.process.slurm_steps import cancel_leftover_steps


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _fake_run(squeue_outputs: list[str]) -> MagicMock:
    """A subprocess.run fake: successive squeue calls return `squeue_outputs` (the last one
    repeats); scancel calls succeed. Every command is recorded in `.commands`."""
    commands: list[list[str]] = []
    state = {"n": 0}

    def run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if cmd[0] == "squeue":
            out = squeue_outputs[min(state["n"], len(squeue_outputs) - 1)]
            state["n"] += 1
            return _completed(out)
        return _completed()

    mock = MagicMock(side_effect=run)
    mock.commands = commands
    return mock


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(slurm_steps.time, "sleep") as sleep:
        yield sleep


def _env(**extra: str):
    base = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    base.update(extra)
    return patch.dict(os.environ, base, clear=True)


def _scancels(run: MagicMock) -> list[list[str]]:
    return [c for c in run.commands if c[0] == "scancel"]


def test_no_slurm_job_runs_nothing() -> None:
    logger = MagicMock()
    with _env(), patch.object(slurm_steps.subprocess, "run") as run:
        assert cancel_leftover_steps(logger) == []
    run.assert_not_called()
    logger.warning.assert_not_called()


def test_running_app_steps_are_cancelled_with_one_warning_each() -> None:
    logger = MagicMock()
    run = _fake_run(["123.batch\n123.extern\n123.4\n123.7\n"])
    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", run):
        result = cancel_leftover_steps(logger)

    assert result == ["123.4", "123.7"]
    assert _scancels(run) == [["scancel", "123.4"], ["scancel", "123.7"]]
    assert logger.warning.call_count == 2
    assert "123.4" in logger.warning.call_args_list[0].args[0]
    assert "123.7" in logger.warning.call_args_list[1].args[0]
    squeue = run.commands[0]
    assert squeue[:2] == ["squeue", "--steps"]
    assert "123" in squeue


def test_step_that_leaves_squeue_during_the_settle_wait_is_not_cancelled() -> None:
    logger = MagicMock()
    run = _fake_run(["123.4\n", ""])
    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", run):
        assert cancel_leftover_steps(logger) == []

    assert _scancels(run) == []
    logger.warning.assert_not_called()


def test_the_workers_own_step_is_never_cancelled() -> None:
    logger = MagicMock()
    run = _fake_run(["123.2\n123.5\n"])
    with _env(SLURM_JOB_ID="123", SLURM_STEP_ID="2"):
        with patch.object(slurm_steps.subprocess, "run", run):
            assert cancel_leftover_steps(logger) == ["123.5"]

    assert _scancels(run) == [["scancel", "123.5"]]


def test_a_clean_listing_costs_one_squeue_and_no_settle(_no_sleep: MagicMock) -> None:
    logger = MagicMock()
    run = _fake_run(["123.batch\n123.extern\n"])
    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", run):
        assert cancel_leftover_steps(logger) == []

    assert len(run.commands) == 1
    _no_sleep.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("squeue"),
        subprocess.TimeoutExpired("squeue", 15),
        _completed("", returncode=1),
    ],
    ids=["missing", "timeout", "nonzero"],
)
def test_squeue_failures_warn_once_and_never_raise(failure) -> None:
    logger = MagicMock()
    if isinstance(failure, Exception):
        run = MagicMock(side_effect=failure)
    else:
        run = MagicMock(return_value=failure)
    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", run):
        assert cancel_leftover_steps(logger) == []

    assert logger.warning.call_count == 1


def test_ids_are_parsed_strictly() -> None:
    logger = MagicMock()
    out = "\n123.interactive\n  123.0  \n\n456.9\n1234.5\n123.batch\n123.12\n"
    run = _fake_run([out])
    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", run):
        assert cancel_leftover_steps(logger) == ["123.0", "123.12"]

    assert _scancels(run) == [["scancel", "123.0"], ["scancel", "123.12"]]


def test_a_scancel_exiting_non_zero_warns_and_is_not_reported_cancelled() -> None:
    logger = MagicMock()
    commands: list[list[str]] = []

    def run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if cmd[0] == "squeue":
            return _completed("123.4\n123.7\n")
        if cmd[1] == "123.4":
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="scancel: error: Access denied\n"
            )
        return _completed()

    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", side_effect=run):
        result = cancel_leftover_steps(logger)

    assert result == ["123.7"]
    assert ["scancel", "123.7"] in commands
    messages = [c.args[0] for c in logger.warning.call_args_list]
    assert (
        "Slurm step 123.4 was still running and scancel failed (exit 1): "
        "scancel: error: Access denied"
    ) in messages
    assert not any("123.4" in m and "cancelled it" in m for m in messages)


def test_a_failing_scancel_warns_and_the_others_still_run() -> None:
    logger = MagicMock()
    commands: list[list[str]] = []

    def run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if cmd[0] == "squeue":
            return _completed("123.4\n123.7\n")
        if cmd[1] == "123.4":
            raise subprocess.TimeoutExpired("scancel", 15)
        return _completed()

    with _env(SLURM_JOB_ID="123"), patch.object(slurm_steps.subprocess, "run", side_effect=run):
        result = cancel_leftover_steps(logger)

    assert result == ["123.7"]
    assert ["scancel", "123.7"] in commands
    assert any("123.4" in c.args[0] for c in logger.warning.call_args_list)
