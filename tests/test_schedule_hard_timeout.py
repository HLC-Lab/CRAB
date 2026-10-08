"""The HARD TIMEOUT branch of `core/experiment/schedule.py` stops the running apps SIGTERM first
(`stop_processes`), and SIGKILLs only a group that is still alive after the grace.

Real `ExperimentRunner` on the direct launcher; a recorder wraps the REAL `os.killpg` (behind the
suite's signal guard), so only groups the test started are ever signalled.
"""

from __future__ import annotations

import csv
import os
import signal
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT
from signal_guard import REAL_KILLPG, guarded

IGNORING_TERM = "echo $$ > {pid}; trap '' TERM; exec sleep 60"
HONOURING_TERM = "echo $$ > {pid}; exec sleep 60"


def _wrapper(path: Path, command: str) -> None:
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        return {command!r}\n\n"
        "    def read_data(self):\n"
        "        return [[1.0]]\n"
    )


def _alive(pid: int) -> bool:
    """Whether `pid` is a live process: a zombie (a grandchild reparented to pytest, which never
    reaps it) is dead for this purpose."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return text[text.rindex(")") + 2 :].split()[0] not in ("Z", "X")


def _run_with_hanging_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> tuple[str, int, list[tuple[int, int]], dict[str, int]]:
    """Run one hanging app until the experiment timeout; return the registry status, the app's
    pid, every (pgid, signal) the recorder saw, and the app's group as it was at the first signal."""
    from crab.core.process import manager

    monkeypatch.setattr(manager, "STOP_GRACE_SECONDS", 1.0)
    pid_file = tmp_path / "app.pid"
    app_path = tmp_path / "hang.py"
    _wrapper(app_path, f"sh -c {script.format(pid=pid_file)!r}")
    real = guarded("killpg", REAL_KILLPG)
    calls: list[tuple[int, int]] = []
    seen: dict[str, int] = {}

    def recorder(pgid: int, sig: int) -> None:
        if "group" not in seen and pid_file.is_file():
            seen["group"] = os.getpgid(int(pid_file.read_text()))
        calls.append((pgid, sig))
        real(pgid, sig)

    monkeypatch.setattr(os, "killpg", recorder)
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": str(app_path), "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1", "timeout": "1"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(output_dir),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(output_dir))
    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    return row["status"], int(pid_file.read_text()), calls, seen


def test_a_hard_timeout_stops_an_app_that_ignores_sigterm_with_sigterm_then_sigkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.monotonic()
    status, app_pid, calls, seen = _run_with_hanging_app(tmp_path, monkeypatch, IGNORING_TERM)

    assert status == "TIMEOUT"
    assert not _alive(app_pid)
    group = seen["group"]
    signals = [sig for pgid, sig in calls if pgid == group and sig != 0]
    assert signals[0] == signal.SIGTERM
    assert signal.SIGKILL in signals
    assert signals.index(signal.SIGTERM) < signals.index(signal.SIGKILL)
    assert time.monotonic() - started < 30  # the 60 s sleep was not waited for


def test_a_hard_timeout_needs_no_sigkill_for_an_app_that_honours_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, app_pid, calls, seen = _run_with_hanging_app(tmp_path, monkeypatch, HONOURING_TERM)

    assert status == "TIMEOUT"
    assert not _alive(app_pid)
    signals = [sig for pgid, sig in calls if pgid == seen["group"] and sig != 0]
    assert signals == [signal.SIGTERM]
