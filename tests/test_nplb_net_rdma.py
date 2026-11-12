"""examples/nplb/net_rdma.sh (`nplb net rdma`): RC and UD perftest per NIC, with a fake ssh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake ssh: HOST COMMAND. Every client (run on nodeA) prints a "BW average" line; the UD server on
# nodeB reports a received rate except on mlx5_2.
FAKE_SSH = r"""#!/bin/bash
host=$1
shift
cmd="$*"
if [[ $host == nodeB ]]; then
    # the receiver of the UD run on mlx5_2 gets nothing; the other UD receivers report a rate
    if [[ $cmd == *"-c UD"* && $cmd != *"-d mlx5_2"* ]]; then
        echo " 2048   1000   12.00   11.50   0.70"
    fi
    exit 0
fi
echo " 65536 BW average[Gb/sec] 390.1"
"""


def run_rdma(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ssh = tmp_path / "fake_ssh"
    ssh.write_text(FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK")}
    env.update(
        SSH=str(ssh),
        RDMA_NICS="mlx5_0 mlx5_2",
        NPLB_REPORT_DIR=str(tmp_path / "reports"),
    )
    return subprocess.run(
        [BASH, str(NPLB / "net_rdma.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_failing_ud_run_is_named_with_its_message(tmp_path: Path) -> None:
    result = run_rdma(tmp_path, "nodeA", "nodeB")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: FAIL 1 of 4 RDMA runs from nodeA to nodeB failed" in result.stdout
    assert "OK rc mlx5_0" in result.stdout
    assert "OK rc mlx5_2" in result.stdout
    assert "FAIL ud mlx5_2: sent, but nodeB received nothing" in result.stdout
    assert "OK ud mlx5_0" in result.stdout


def test_one_host_is_a_usage_error(tmp_path: Path) -> None:
    result = run_rdma(tmp_path, "nodeA")
    assert result.returncode == 2
    assert "usage: nplb net rdma" in result.stderr
