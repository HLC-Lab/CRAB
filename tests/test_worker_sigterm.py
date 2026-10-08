"""A worker that gets SIGTERM stops its apps, skips the rest and exits 143.

Before, the worker died at once and the app it had started kept running (the orphan the local
scheduler's cancel would leave behind). Runs ``execute_worker`` in a real subprocess; the only
signals sent go to processes this test started.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.execution.settings import to_json
from settings_fixtures import LOCAL_DIRECT

SRC = str(Path(__file__).resolve().parent.parent / "src")
DRIVER = "import sys; from crab.cli.orchestrator import execute_worker; execute_worker(sys.argv[1])"


def _alive(pid: int) -> bool:
    """Whether `pid` is a live, non-zombie process."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _work_dir(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    pid_file = tmp_path / "app.pid"
    (tmp_path / "app.py").write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def run_app(self):\n"
        f"        return \"sh -c 'echo $$ > {pid_file}; exec sleep 60'\"\n"
    )
    config = {
        "global_options": {"numnodes": "1"},
        "experiments": {
            "exp1": {
                "apps": {"0": {"path": str(tmp_path / "app.py")}},
                "local_options": {"minruns": "1", "maxruns": "1", "timeout": "120"},
            },
            "exp2": {
                "apps": {"0": {"path": str(tmp_path / "app.py")}},
                "local_options": {"minruns": "1", "maxruns": "1", "timeout": "120"},
            },
        },
    }
    (work / "config.json").write_text(json.dumps(config))
    (work / "environment.json").write_text("{}")
    (work / "execution.json").write_text(json.dumps(to_json(LOCAL_DIRECT)))
    return work


def test_sigterm_stops_the_app_and_exits_143(tmp_path: Path) -> None:
    work = _work_dir(tmp_path)
    pid_file = tmp_path / "app.pid"
    worker = subprocess.Popen(
        [sys.executable, "-c", DRIVER, str(work)],
        env={**os.environ, "PYTHONPATH": SRC},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    app_pid = 0
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and worker.poll() is None:
            if pid_file.exists() and pid_file.read_text().strip():
                app_pid = int(pid_file.read_text())
                break
            time.sleep(0.05)
        assert app_pid, f"the app never started; worker rc={worker.poll()}"
        assert _alive(app_pid)

        worker.send_signal(signal.SIGTERM)
        out, err = worker.communicate(timeout=15)

        assert worker.returncode == 143, (out, err)
        assert "Worker terminated (SIGTERM)" in out + err
        assert not _alive(app_pid), "the app outlived its worker"
        # The second experiment never started: its app would have rewritten the pid file.
        assert int(pid_file.read_text()) == app_pid
        assert "Starting experiment [2/2]" not in out + err
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        if app_pid and _alive(app_pid):
            os.kill(app_pid, signal.SIGKILL)


IGNORE_APP = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"
# Shortens the grace and, when asked, removes every teardown but the worker's own safety net.
GRACE_DRIVER = (
    "import sys\n"
    "import crab.core.process.manager as manager\n"
    "manager.STOP_GRACE_SECONDS = 3.0\n"
    "if sys.argv[2] == 'no-teardown':\n"
    "    from crab.core.experiment.runner import ExperimentRunner\n"
    "    ExperimentRunner.teardown = lambda self: None\n"
    "from crab.cli.orchestrator import execute_worker\n"
    "execute_worker(sys.argv[1])\n"
)


def _ignoring_work_dir(tmp_path: Path) -> Path:
    """A worker dir whose one app ignores SIGTERM and is stopped by its scheduled end at 1 s."""
    work = tmp_path / "work"
    work.mkdir()
    pid_file = tmp_path / "app.pid"
    (tmp_path / "ignore.py").write_text(IGNORE_APP)
    (tmp_path / "app.py").write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def run_app(self):\n"
        # The wrapper script is bash: `exec` makes the SIGTERM-ignoring app the group leader.
        f'        return "echo $$ > {pid_file}; exec {sys.executable} {tmp_path}/ignore.py"\n'
    )
    config = {
        "global_options": {"numnodes": "1"},
        "experiments": {
            "exp1": {
                "apps": {"0": {"path": str(tmp_path / "app.py"), "end": "1"}},
                "local_options": {"minruns": "1", "maxruns": "1", "timeout": "120"},
            }
        },
    }
    (work / "config.json").write_text(json.dumps(config))
    (work / "environment.json").write_text("{}")
    (work / "execution.json").write_text(json.dumps(to_json(LOCAL_DIRECT)))
    return work


def _sigterm_twice_during_a_stop(tmp_path: Path, mode: str) -> None:
    work = _ignoring_work_dir(tmp_path)
    pid_file = tmp_path / "app.pid"
    worker = subprocess.Popen(
        [sys.executable, "-c", GRACE_DRIVER, str(work), mode],
        env={**os.environ, "PYTHONPATH": SRC},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    app_pid = 0
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and worker.poll() is None:
            if pid_file.exists() and pid_file.read_text().strip():
                app_pid = int(pid_file.read_text())
                break
            time.sleep(0.05)
        assert app_pid, f"the app never started; worker rc={worker.poll()}"

        # The scheduled end (1 s) starts a stop that waits out the 3 s grace on an app that
        # ignores SIGTERM; both signals land inside that wait.
        time.sleep(1.8)
        worker.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        worker.send_signal(signal.SIGTERM)
        out, err = worker.communicate(timeout=20)

        assert worker.returncode == 143, (out, err)
        assert "Worker terminated (SIGTERM)" in out + err
        assert not _alive(app_pid), "the app outlived its worker"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        if app_pid and _alive(app_pid):
            os.kill(app_pid, signal.SIGKILL)


def test_two_sigterms_while_an_app_is_being_stopped_leave_no_app(tmp_path: Path) -> None:
    _sigterm_twice_during_a_stop(tmp_path, "normal")


def test_the_worker_stops_registered_apps_even_when_no_teardown_runs(tmp_path: Path) -> None:
    """The safety net: the stop that SIGTERM interrupted is finished by the worker itself."""
    _sigterm_twice_during_a_stop(tmp_path, "no-teardown")


def test_a_worker_whose_supervisor_pipe_closes_stops_its_app_and_exits_143(tmp_path: Path) -> None:
    """`CRAB_SUPERVISOR_FD` is the read end of a pipe whose write end the supervisor keeps for its
    life. Closing the write end (the supervisor died) makes the worker SIGTERM itself: the same
    teardown as a cancel, so no app is left behind."""
    work = _work_dir(tmp_path)
    pid_file = tmp_path / "app.pid"
    read_fd, write_fd = os.pipe()
    worker = subprocess.Popen(
        [sys.executable, "-c", DRIVER, str(work)],
        env={**os.environ, "PYTHONPATH": SRC, "CRAB_SUPERVISOR_FD": str(read_fd)},
        pass_fds=(read_fd,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(read_fd)
    app_pid = 0
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and worker.poll() is None:
            if pid_file.exists() and pid_file.read_text().strip():
                app_pid = int(pid_file.read_text())
                break
            time.sleep(0.05)
        assert app_pid, f"the app never started; worker rc={worker.poll()}"
        assert worker.poll() is None, "the worker must keep running while the pipe is open"

        os.close(write_fd)
        write_fd = -1
        out, err = worker.communicate(timeout=20)

        assert worker.returncode == 143, (out, err)
        assert "Supervisor is gone; stopping the job" in out + err
        assert not _alive(app_pid), "the app outlived its worker"
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        if app_pid and _alive(app_pid):
            os.kill(app_pid, signal.SIGKILL)


class _ThreadRecorder:
    """Stands in for `threading.Thread`: records the start instead of blocking on a pipe."""

    started: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def start(self) -> None:
        self.started.append(self.kwargs)


@pytest.fixture
def recorded_threads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    from crab.cli import orchestrator

    _ThreadRecorder.started = []
    monkeypatch.setattr(orchestrator.threading, "Thread", _ThreadRecorder)
    return _ThreadRecorder.started


def test_without_the_supervisor_variable_the_watch_does_nothing(
    monkeypatch: pytest.MonkeyPatch, recorded_threads: list[dict[str, object]]
) -> None:
    """The Slurm worker: no thread and no warning."""
    from crab.cli.orchestrator import _watch_supervisor

    monkeypatch.delenv("CRAB_SUPERVISOR_FD", raising=False)
    logger = MagicMock()

    _watch_supervisor(logger)

    assert recorded_threads == []
    logger.warning.assert_not_called()


@pytest.mark.parametrize("value", ["not-a-number", "99999"], ids=["bad-number", "closed-fd"])
def test_a_bad_supervisor_variable_warns_and_is_removed(
    monkeypatch: pytest.MonkeyPatch, recorded_threads: list[dict[str, object]], value: str
) -> None:
    from crab.cli.orchestrator import _watch_supervisor

    monkeypatch.setenv("CRAB_SUPERVISOR_FD", value)
    logger = MagicMock()

    _watch_supervisor(logger)

    assert recorded_threads == []
    logger.warning.assert_called_once()
    assert "CRAB_SUPERVISOR_FD" in logger.warning.call_args[0][0]
    assert "CRAB_SUPERVISOR_FD" not in os.environ


def test_a_good_supervisor_variable_starts_the_watch_and_is_removed(
    monkeypatch: pytest.MonkeyPatch, recorded_threads: list[dict[str, object]]
) -> None:
    """Apps are started with this environment, so a stale descriptor number must not be in it."""
    from crab.cli.orchestrator import _watch_supervisor

    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("CRAB_SUPERVISOR_FD", str(read_fd))
        logger = MagicMock()

        _watch_supervisor(logger)

        assert len(recorded_threads) == 1
        assert recorded_threads[0]["daemon"] is True
        assert not os.get_inheritable(read_fd)
        logger.warning.assert_not_called()
        assert "CRAB_SUPERVISOR_FD" not in os.environ
    finally:
        os.close(read_fd)
        os.close(write_fd)
