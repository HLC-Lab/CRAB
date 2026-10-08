"""Schedule building and the event loop of a single run of an experiment."""

import os
import signal
import time
from dataclasses import dataclass
from typing import Any

from ..process import end_job, run_job
from .failures import write_error_log


@dataclass
class Schedule:
    """How the apps of an experiment start and stop within one run."""

    dependency_map: dict[int, int]
    static_schedule: list[tuple[int, str, float]]
    rel_durations: dict[int, float]


@dataclass
class RunOutcome:
    """What the event loop of one run reports back to the run loop."""

    run_successful: bool
    experiment_status: str


def build_schedule(apps: list[Any]) -> Schedule:
    # Schedule Logic Preparation
    dependency_map = {}
    static_schedule = []
    rel_durations = {}

    # Build Schedule
    for i, app in enumerate(apps):
        # Start
        if app.start_string.startswith("s"):
            dependency_map[i] = int(app.start_string[1:])
        else:
            static_schedule.append((i, "s", float(app.start_string)))

        # End
        if app.config_end and app.config_end != "f":
            val = float(app.config_end)
            if app.start_string.startswith("s"):
                rel_durations[i] = val
            else:
                static_schedule.append((i, "k", val))

    return Schedule(dependency_map, static_schedule, rel_durations)


def run_events(
    apps: list[Any],
    launch_mode: str,
    ppn: int,
    schedule: Schedule,
    run_log: Any,
    data_path: Any,
    exp_dir: str,
    timeout: float,
    global_start: float,
    run_start: float,
    experiment_status: str,
) -> RunOutcome:
    """Runs the apps of one run until every one is done or the hard timeout hits."""
    dependency_map = schedule.dependency_map
    static_schedule = schedule.static_schedule
    rel_durations = schedule.rel_durations
    run_successful = True

    # Reset ephemeral schedule for this run
    curr_schedule = sorted(static_schedule, key=lambda x: x[2])
    curr_deps = dependency_map.copy()
    running = set()
    finished = set()

    f_app_ids = {i for i, app in enumerate(apps) if str(app.config_end) == "f"}

    # Inner Event Loop
    while True:
        now = time.time() - run_start

        # 1. Time-based events
        while curr_schedule and curr_schedule[0][2] <= now:
            aid, action, _ = curr_schedule.pop(0)
            if action == "s":
                if aid not in running:
                    app_log = run_log.enter(f"App {aid}")
                    concurrent = len(static_schedule) > 1 or len(dependency_map) > 0

                    # --- Merge Hooks and Override Launcher ---
                    merged_pre_commands = apps[aid].get_pre_commands()
                    launcher_override = apps[aid].get_launcher_override()

                    run_job(
                        apps[aid],
                        launch_mode,
                        ppn,
                        logger=app_log,
                        pre_commands=merged_pre_commands,
                        live_stream=concurrent,
                        data_path=data_path,
                        launcher=launcher_override,
                    )

                    running.add(aid)
            elif action == "k":
                if aid in running:
                    end_job(apps[aid], run_log)
                    running.remove(aid)
                    finished.add(aid)
        # 2. Check process status
        for aid in list(running):
            proc = apps[aid].process
            if proc.poll() is not None:
                app_log = run_log.enter(f"App {aid}")
                try:
                    # Ensure silent thread is finished reading
                    if hasattr(apps[aid], "_stream_thread") and apps[aid]._stream_thread:
                        apps[aid]._stream_thread.join(timeout=2.0)

                    # stdout is already consumed by the thread, so communicate() only gets stderr
                    _, err = proc.communicate()

                    # Reconstruct stdout from the buffer
                    out = b"".join(getattr(apps[aid], "raw_stdout_buffer", []))

                    apps[aid].set_output(out, err)

                    exit_code = proc.returncode
                    if exit_code != 0:
                        app_log.error(f"FAILED  exit={exit_code}")
                        run_successful = False

                        if experiment_status != "TIMEOUT":
                            experiment_status = "FAILED"

                        # Extract both streams
                        stdout_text = (
                            out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
                        )
                        stderr_text = (
                            err.decode("utf-8", errors="replace") if isinstance(err, bytes) else err
                        )

                        # Forward BOTH streams to the console if they exist
                        if stdout_text.strip():
                            app_log.app_output("STDOUT Dump:", stdout_text)
                        if stderr_text and stderr_text.strip():
                            app_log.app_output("STDERR Dump:", stderr_text)

                        write_error_log(exp_dir, aid, exit_code, stdout_text, stderr_text, app_log)
                    else:
                        app_log.info("FINISHED  exit=0")
                        # REMOVED: The logic that forwarded 'out' to app_log.app_output
                        # Data is now silently waiting in self.apps[aid].stdout for the CSV parser.

                except Exception as e:
                    app_log.error(f"Failed reading output: {e}")
                    run_successful = False
                    if experiment_status != "TIMEOUT":
                        experiment_status = "FAILED"
                    apps[aid].process = None  # nothing readable to collect

                running.remove(aid)
                finished.add(aid)

        # 3. Check Dependencies
        started_deps = []
        for waiter, target in curr_deps.items():
            if target in finished:
                dep_log = run_log.enter(f"App {waiter}")

                # --- Merge Hooks and Override Launcher ---
                merged_pre_commands = apps[waiter].get_pre_commands()
                launcher_override = apps[waiter].get_launcher_override()

                run_job(
                    apps[waiter],
                    launch_mode,
                    ppn,
                    logger=dep_log,
                    pre_commands=merged_pre_commands,
                    live_stream=True,
                    data_path=data_path,
                    launcher=launcher_override,
                )

                running.add(waiter)
                if waiter in rel_durations:
                    curr_schedule.append((waiter, "k", now + rel_durations[waiter]))
                    curr_schedule.sort(key=lambda x: x[2])
                started_deps.append(waiter)
        for s in started_deps:
            del curr_deps[s]

        if not curr_schedule and not curr_deps and not (running - f_app_ids):
            break

        # Check if the global elapsed time has exceeded the timeout
        if (time.time() - global_start) >= timeout:
            run_log.error(f"HARD TIMEOUT: Experiment exceeded {timeout}s mid-run.")
            experiment_status = "TIMEOUT"
            for active_aid in list(running):
                try:
                    os.killpg(os.getpgid(apps[active_aid].process.pid), signal.SIGKILL)
                except OSError:
                    pass
            break  # Break the inner loop, forcing a teardown

        time.sleep(0.05)

    return RunOutcome(run_successful, experiment_status)


def kill_f_apps(apps: list[Any], run_log: Any) -> None:
    # ── Lorenzo's modifications ──────────────────────────────
    # Kill "f" apps now that all other work is done
    for app in apps:
        if str(app.config_end) == "f":
            if app.process is not None and app.process.poll() is None:
                end_job(app, run_log)
    # ─────────────────────────────────────────────────────────
