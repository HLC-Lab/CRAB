"""examples/nplb/node_procs.sh (`nplb node procs|stop`): leftover benchmark processes, with a fake ssh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake ssh: HOST COMMAND. node1 runs one leftover Blink process, node2 none. Every call is logged.
FAKE_SSH = r"""#!/bin/bash
host=$1
shift
echo "$host $*" >>"$FAKE_SSH_LOG"
case $* in
    "ps -eo pid,etimes,args | grep -E"*)
        [[ $host == node1 ]] && echo "4242 900 /root/crab/lab/blink-gpu/build-cuda/bin/alltoall_comm_only -endl"
        exit 0
        ;;
    "ps -eo args | grep -c"*)
        echo 0
        ;;
esac
exit 0
"""


def run_procs(
    tmp_path: Path, *args: str, answer: str = "", **env_extra: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    ssh = tmp_path / "fake_ssh"
    ssh.write_text(FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    log = tmp_path / "ssh.log"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK", "NPLB_YES")}
    env.update(SSH=str(ssh), FAKE_SSH_LOG=str(log), NPLB_REPORT_DIR=str(tmp_path / "reports"))
    env.update(env_extra)
    result = subprocess.run(
        [BASH, str(NPLB / "node_procs.sh"), *args],
        env=env,
        input=answer,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


def test_procs_lists_the_leftover_and_warns(tmp_path: Path) -> None:
    result, _ = run_procs(tmp_path, "procs", "node1", "node2")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: WARN 1 benchmark processes on node1 node2" in result.stdout
    assert (
        "node1 4242 900 /root/crab/lab/blink-gpu/build-cuda/bin/alltoall_comm_only" in result.stdout
    )


def test_procs_is_ok_when_nothing_is_left(tmp_path: Path) -> None:
    result, _ = run_procs(tmp_path, "procs", "node2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERDICT: OK no leftover benchmark process on node2" in result.stdout


def test_stop_asks_first_and_sends_signals_only_after_a_yes(tmp_path: Path) -> None:
    declined, calls = run_procs(tmp_path, "stop", "node1", answer="n\n")
    assert declined.returncode == 1
    assert "declined: nothing changed" in declined.stdout
    assert not any("pkill" in call for call in calls)


def test_stop_with_yes_kills_and_counts_what_is_left(tmp_path: Path) -> None:
    result, calls = run_procs(tmp_path, "stop", "node1", NPLB_YES="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stopped: 1 processes sent a signal, 0 left on node1" in result.stdout
    assert any(call.startswith("node1 pkill -TERM") for call in calls)


def test_a_bad_verb_is_a_usage_error(tmp_path: Path) -> None:
    result, _ = run_procs(tmp_path, "wipe")
    assert result.returncode == 2
    assert "usage: nplb node procs" in result.stderr
