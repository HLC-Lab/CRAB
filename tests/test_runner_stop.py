"""The runner stops running apps through `stop_process_groups` (SIGTERM, one shared grace,
SIGKILL fallback), never with a direct SIGKILL: SIGKILL takes srun down before it can end its
Slurm step. Apps stopped at the same moment go in one batch, so the grace is paid once.

Fake processes never reach a real os.killpg: `stop_process_groups` and `os.killpg` are patched.
The execute() tests use real processes the runner itself starts (own session, stopped by the
runner), through the local launcher.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from crab.core.experiment import runner as runner_module
from crab.core.experiment.runner import ExperimentRunner
from crab.core.process import stop_process_groups as real_stop_process_groups


def _fake_app(running: bool, pid: int = 424242) -> MagicMock:
    app = MagicMock()
    app.process.pid = pid
    app.process.poll.return_value = None if running else 0
    return app


def _bare_runner(apps: list) -> ExperimentRunner:
    runner = ExperimentRunner.__new__(ExperimentRunner)
    runner.apps = apps
    runner.log = MagicMock()
    return runner


def test_teardown_stops_the_running_apps_in_one_batch() -> None:
    running_a, finished, running_b = _fake_app(True, 11), _fake_app(False, 12), _fake_app(True, 13)
    runner = _bare_runner([running_a, finished, running_b])

    with (
        patch.object(runner_module, "stop_process_groups") as stop,
        patch("os.killpg") as killpg,
    ):
        runner.teardown()

    assert stop.call_count == 1
    assert stop.call_args.args[0] == [running_a.process, running_b.process]
    killpg.assert_not_called()


def test_teardown_with_nothing_running_stops_nothing() -> None:
    runner = _bare_runner([_fake_app(False), _fake_app(False)])
    runner.apps[1].process = None

    with patch.object(runner_module, "stop_process_groups") as stop:
        runner.teardown()

    stop.assert_not_called()


def test_stop_apps_skips_apps_without_a_running_process() -> None:
    running, finished, never_started = _fake_app(True), _fake_app(False), _fake_app(True)
    never_started.process = None
    runner = _bare_runner([running, finished, never_started])

    with patch.object(runner_module, "stop_process_groups") as stop:
        runner._stop_apps(runner.apps, runner.log)

    stop.assert_called_once()
    assert stop.call_args.args[0] == [running.process]
    assert stop.call_args.kwargs["logger"] is runner.log


def test_teardown_cancels_leftover_slurm_steps_after_stopping_apps() -> None:
    runner = _bare_runner([_fake_app(True, 11)])
    order: list[str] = []

    def fake_stop(*args, **kwargs):
        order.append("stop")

    def fake_cancel(*args, **kwargs):
        order.append("cancel")
        return []

    with (
        patch.object(runner_module, "stop_process_groups", side_effect=fake_stop),
        patch.object(runner_module, "cancel_leftover_steps", side_effect=fake_cancel) as cancel,
    ):
        runner.teardown()

    assert order == ["stop", "cancel"]
    assert cancel.call_args.args[0] is runner.log


def test_runner_source_has_no_direct_kill() -> None:
    source = inspect.getsource(runner_module)
    assert "signal.SIGKILL" not in source
    assert "os.killpg(" not in source


# --- execute(): the real run loop with the local launcher ---------------------------------------


def _wrapper(path: Path, command: str) -> None:
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = []\n\n"
        "    def run_app(self):\n"
        f"        return {command!r}\n\n"
        "    def read_data(self):\n"
        "        return []\n"
    )


def _execute(tmp_path: Path, monkeypatch, apps: dict, local_options: dict) -> list:
    """Run the experiment; return its stop events in order.

    An event is ("stop", n_processes) for a `stop_process_groups` call, ("cancel",) for a
    `cancel_leftover_steps` call or ("teardown",).
    Teardown is replaced by a recorder, so a stop that the run loop skips is not hidden by
    teardown stopping the same apps; the processes are cleaned up here afterwards. The stop
    spy delegates to the real function: the processes are the runner's own.
    """
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": local_options},
        global_options={"numnodes": str(len(apps))},
        node_list=[f"n{i}" for i in range(len(apps))],
        output_dir=str(output_dir),
        logger=MagicMock(),
    )
    runner.setup()
    events: list = []
    real_teardown = ExperimentRunner.teardown

    def spy(processes, *args, **kwargs):
        events.append(("stop", len(processes)))
        return real_stop_process_groups(processes, *args, **kwargs)

    def record_cancel(*args, **kwargs):
        events.append(("cancel",))
        return []

    def record_teardown(self):
        events.append(("teardown",))

    try:
        with (
            patch.object(runner_module, "stop_process_groups", side_effect=spy),
            patch.object(runner_module, "cancel_leftover_steps", side_effect=record_cancel),
            patch.object(ExperimentRunner, "teardown", record_teardown),
        ):
            runner.execute(str(output_dir))
    finally:
        real_teardown(runner)
    return events


def test_hard_timeout_stops_every_running_app_in_one_call(tmp_path: Path, monkeypatch) -> None:
    for name in ("a", "b"):
        _wrapper(tmp_path / f"{name}.py", "sleep 30")

    events = _execute(
        tmp_path,
        monkeypatch,
        apps={
            "0": {"path": str(tmp_path / "a.py")},
            "1": {"path": str(tmp_path / "b.py")},
        },
        local_options={"minruns": "1", "maxruns": "1", "timeout": "2"},
    )

    # The stop comes from the timeout branch itself, before teardown runs; the step cleanup
    # follows once per run.
    assert events == [("stop", 2), ("cancel",), ("teardown",)]


def test_f_apps_still_running_are_stopped_in_one_call_then_collected(
    tmp_path: Path, monkeypatch
) -> None:
    _wrapper(tmp_path / "main.py", "sleep 0.5")
    _wrapper(tmp_path / "quick.py", "echo done")
    _wrapper(tmp_path / "bg.py", "sleep 30")
    apps = {
        "0": {"path": str(tmp_path / "main.py")},
        "1": {"path": str(tmp_path / "bg.py"), "end": "f"},
        "2": {"path": str(tmp_path / "quick.py"), "end": "f"},
        "3": {"path": str(tmp_path / "bg.py"), "end": "f"},
    }
    ended: list[int] = []
    real_end_job = runner_module.end_job

    def spy_end_job(app, logger):
        ended.append(app.id_num)
        real_end_job(app, logger)

    with patch.object(runner_module, "end_job", side_effect=spy_end_job):
        events = _execute(
            tmp_path,
            monkeypatch,
            apps=apps,
            local_options={"minruns": "1", "maxruns": "1", "timeout": "30"},
        )

    # One batch holds the two long-running f-apps; the one that exited on its own is not in it.
    assert events == [("stop", 2), ("cancel",), ("teardown",)]
    # Each running f-app still goes through end_job to collect its output.
    assert sorted(ended) == [1, 3]
