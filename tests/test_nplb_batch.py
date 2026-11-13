"""examples/nplb/batch_run.sh (`nplb batch`): rows one by one in a background runner, with a fake nplb."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake nplb: logs every call; row 2 fails, row 9 sleeps until the file "release" exists.
FAKE_NPLB = r"""#!/bin/bash
echo "$*" >>"$FAKE_LOG"
case $* in
    "run 2") echo "VERDICT: REFUSED row 2"; exit 1 ;;
    "run 9") while [[ ! -e $FAKE_RELEASE ]]; do sleep 0.1; done ;;
    report) echo "report page" ;;
esac
exit 0
"""


def make_env(tmp_path: Path) -> dict[str, str]:
    fake = tmp_path / "fake_nplb"
    fake.write_text(FAKE_NPLB, encoding="utf-8")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK", "NPLB_YES")}
    env.update(
        WORK=str(tmp_path / "work"),
        NPLB_CMD=f"{BASH} {fake}",
        FAKE_LOG=str(tmp_path / "calls.log"),
        FAKE_RELEASE=str(tmp_path / "release"),
        NPLB_REPORT_DIR=str(tmp_path / "reports"),
    )
    return env


def batch(env: dict[str, str], *args: str, answer: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(NPLB / "batch_run.sh"), *args],
        env=env,
        input=answer,
        capture_output=True,
        text=True,
        timeout=60,
    )


def wait_for(cond, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def current_log(env: dict[str, str]) -> Path:
    batch_dir = Path(env["WORK"]) / "batch"
    return batch_dir / ((batch_dir / "current").read_text().strip() + ".log")


def test_rows_run_in_order_a_failure_does_not_stop_the_batch(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    result = batch(env, "start", "1-3", "5")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "started: rows 1 2 3 5 x 1 rounds in the background" in result.stdout
    log = current_log(env)
    wait_for(lambda: log.exists() and log.read_text().endswith("batch: end\n"), "the batch end")
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls == ["run 1", "run 2", "run 3", "run 5", "report"]
    status = batch(env, "status")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "VERDICT: FINISHED" in status.stdout
    assert "4 rows ended" in status.stdout
    assert "(refused or failed: see the log)" in status.stdout
    assert "VERDICT: REFUSED row 2" in status.stdout


def test_a_second_start_is_refused_while_running_and_stop_ends_it(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert batch(env, "start", "9", "10").returncode == 0
    wait_for(
        lambda: (
            "run 9" in (tmp_path / "calls.log").read_text("utf-8")
            if (tmp_path / "calls.log").exists()
            else False
        ),
        "row 9 to start",
    )
    again = batch(env, "start", "4")
    assert again.returncode == 1
    assert "REFUSED: a batch is already running" in again.stdout
    assert "VERDICT: RUNNING" in batch(env, "status").stdout
    declined = batch(env, "stop", answer="n\n")
    assert declined.returncode == 1 and "declined" in declined.stdout
    stopped = batch(env, "stop", answer="y\n")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert "VERDICT: DIED" in batch(env, "status").stdout
    assert "run 10" not in (tmp_path / "calls.log").read_text("utf-8")


def test_bad_rows_and_no_batch(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert batch(env, "start", "x").returncode == 2
    assert batch(env, "start", "5-3").returncode == 2
    assert batch(env, "start").returncode == 2
    none = batch(env, "status")
    assert none.returncode == 1 and "no batch yet" in none.stdout


def test_repeat_runs_the_rows_again_with_force(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    result = batch(env, "start", "--repeat", "2", "1", "3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "started: rows 1 3 x 2 rounds" in result.stdout
    log = current_log(env)
    wait_for(lambda: log.exists() and log.read_text().endswith("batch: end\n"), "the batch end")
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls == ["run 1", "run 3", "run 1 --force", "run 3 --force", "report"]
    assert "4 rows ended" in batch(env, "status").stdout


def test_repeat_needs_a_positive_number(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert batch(env, "start", "--repeat", "0", "1").returncode == 2
    assert batch(env, "start", "--repeat", "x", "1").returncode == 2


def test_status_with_a_stale_pid_file_prints_no_error(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    result = batch(env, "start", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    log = current_log(env)
    wait_for(lambda: log.exists() and log.read_text().endswith("batch: end\n"), "the batch end")
    # a pid that cannot exist (above pid_max): the runner is gone, runner.pid stayed
    (Path(env["WORK"]) / "batch" / "runner.pid").write_text("99999999\n")
    status = batch(env, "status")
    assert "VERDICT: FINISHED" in status.stdout
    assert "/cmdline" not in status.stdout + status.stderr
