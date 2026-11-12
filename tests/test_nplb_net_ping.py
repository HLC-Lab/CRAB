"""examples/nplb/net_ping.sh (`nplb net ping`): per-NIC ping between two nodes, with a fake ssh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake ssh: HOST COMMAND. Both hosts have eth0 (management, default route) and rail0, rail1; a ping
# from rail1 to the other host fails.
FAKE_SSH = r"""#!/bin/bash
host=$1
shift
cmd="$*"
case $cmd in
    "ip -4 -o addr show up scope global")
        n=${host#node}
        echo "2: eth0    inet 10.0.0.$n/24 brd 10.0.0.255 scope global eth0"
        echo "3: rail0    inet 10.1.0.$n/24 brd 10.1.0.255 scope global rail0"
        echo "4: rail1    inet 10.2.0.$n/24 brd 10.2.0.255 scope global rail1"
        ;;
    "ip route show default")
        echo "default via 10.0.0.1 dev eth0 proto static"
        ;;
    ping*)
        [[ $cmd == *"-I rail1"* ]] && exit 1
        exit 0
        ;;
esac
"""


def run_ping(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ssh = tmp_path / "fake_ssh"
    ssh.write_text(FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK")}
    env.update(SSH=str(ssh), NPLB_REPORT_DIR=str(tmp_path / "reports"))
    return subprocess.run(
        [BASH, str(NPLB / "net_ping.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_failing_rail_is_named_and_the_management_netdev_is_skipped(tmp_path: Path) -> None:
    result = run_ping(tmp_path, "node1", "node2")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: FAIL 1 of 2 NICs of node1 cannot reach node2" in result.stdout
    assert "OK rail0 10.1.0.1 -> 10.1.0.2" in result.stdout
    assert "FAIL rail1 10.2.0.1 -> 10.2.0.2" in result.stdout
    assert "eth0" not in result.stdout


def test_one_host_is_a_usage_error(tmp_path: Path) -> None:
    result = run_ping(tmp_path, "node1")
    assert result.returncode == 2
    assert "usage: nplb net ping" in result.stderr
