"""`crab cancel` of a local job, end to end with REAL processes (the 2026-10-08 repro).

A local job is submitted through the real engine and the real `LocalScheduler`: a detached
supervisor, a real `crab worker`, and an app that is a `sleep 60`. After `gather_cancel` no process
of the job may be left (the supervisor, the worker and the app all stop) and the job reads
`CANCELLED`. Before B8 the supervisor's group was SIGTERMed with no handler: the worker died at
once, the app kept running, and the job read `LOST`.

Signals only ever go to groups this test started (the teardown), through the signal guard.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from crab.cli.contract import gather_cancel, gather_status
from crab.core.engine import Engine
from crab.core.execution.scheduler import local_state
from crab.log import CrabLogger
from settings_fixtures import LOCAL_DIRECT
from signal_guard import REAL_KILLPG, guarded

_killpg = guarded("killpg", REAL_KILLPG)

pytestmark = pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="needs /proc")


def _poll(check: Callable[[], Any], what: str, timeout: float) -> Any:
    end = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= end:
            pytest.fail(f"timed out after {timeout}s waiting for: {what}", pytrace=False)
        time.sleep(0.05)


def _stat_fields(pid: int) -> tuple[str, int] | None:
    """The state and the process group of `pid`, or None when it does not exist."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    state, _ppid, group = text[text.rindex(")") + 2 :].split()[:3]
    return state, int(group)


def _alive(pid: int) -> bool:
    fields = _stat_fields(pid)
    return fields is not None and fields[0] != "Z"


def _live_members(pgid: int) -> list[int]:
    """The pids of the non-zombie processes in group `pgid`."""
    members: list[int] = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
        except OSError:
            continue
        state, _ppid, group = text[text.rindex(")") + 2 :].split()[:3]
        if int(group) == pgid and state != "Z":
            members.append(int(stat.parent.name))
    return members


@pytest.fixture
def started_groups() -> Iterator[list[int]]:
    """Process groups this test started; SIGKILLed at teardown whatever the test did."""
    groups: list[int] = []
    try:
        yield groups
    finally:
        for pgid in groups:
            if pgid > 1 and _live_members(pgid):
                try:
                    _killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def _config(tmp_path: Path) -> dict[str, Any]:
    pid_file = tmp_path / "app.pid"
    (tmp_path / "app.py").write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def run_app(self):\n"
        f"        return \"sh -c 'echo $$ > {pid_file}; exec sleep 60'\"\n"
    )
    return {
        "global_options": {"numnodes": "1", "datapath": str(tmp_path / "data")},
        "experiments": {
            "exp1": {
                "apps": {"0": {"path": str(tmp_path / "app.py")}},
                "local_options": {"minruns": "1", "maxruns": "1", "timeout": "600"},
            }
        },
    }


def test_cancelling_a_running_local_job_leaves_no_process_and_reads_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started_groups: list[int]
) -> None:
    crab_bin = Path(sys.executable).parent / "crab"
    assert crab_bin.is_file(), crab_bin
    monkeypatch.setattr(sys, "argv", [str(crab_bin), "run"])

    result = Engine(CrabLogger())._run_orchestrator(_config(tmp_path), {}, settings=LOCAL_DIRECT)
    job_id = result["job_id"]
    record = local_state.read_record(local_state.jobs_dir(os.environ), job_id)
    assert record is not None
    supervisor_pid = record.supervisor_pid
    started_groups.append(supervisor_pid)

    pid_file = tmp_path / "app.pid"
    app_pid = _poll(
        lambda: int(pid_file.read_text()) if pid_file.exists() and pid_file.read_text() else 0,
        "the app to start",
        timeout=30,
    )
    started_groups.append(app_pid)
    assert _alive(app_pid)
    # The worker is the live process of this job that is in neither the supervisor's group nor
    # the app's: its parent is the supervisor.
    worker_pids = [
        int(stat.parent.name)
        for stat in Path("/proc").glob("[0-9]*/stat")
        if _is_child_of(stat, supervisor_pid)
    ]
    assert len(worker_pids) == 1, worker_pids
    worker_group = _stat_fields(worker_pids[0])
    assert worker_group is not None
    started_groups.append(worker_group[1])

    assert gather_cancel(job_id)["cancelled"] is True

    _poll(lambda: not _alive(app_pid), "the app to be gone", timeout=30)
    _poll(
        lambda: not _live_members(supervisor_pid) and not _live_members(worker_group[1]),
        "the supervisor's and the worker's groups to be empty",
        timeout=30,
    )
    status = gather_status([job_id])["jobs"][0]
    assert status["state"] == "CANCELLED", status
    state = json.loads((Path(result["data_dir"]) / "local_state.json").read_text())
    assert state["state"] == "CANCELLED"


def _is_child_of(stat: Path, ppid: int) -> bool:
    try:
        text = stat.read_text()
    except OSError:
        return False
    fields = text[text.rindex(")") + 2 :].split()
    return int(fields[1]) == ppid and fields[0] != "Z"
