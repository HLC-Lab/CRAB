"""examples/nplb/node_scan.sh (`nplb node scan`): every Slurm node, asked over a fake ssh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake sinfo: node1 in two partitions, node2 and node3 in one. Fake ssh: node1 is a GPU node,
# node2 has no GPU, node3 does not answer.
FAKE_SINFO = """#!/bin/bash
printf '%s\\n' "node1 gpu* idle" "node1 other idle" "node2 cpu mix" "node3 gpu down"
"""
FAKE_SSH = r"""#!/bin/bash
for a in "$@"; do host=$a; case $a in node*) break ;; esac; done
case $host in
    node1) echo "gpus=8 rdma=16 crab=yes load=0.10" ;;
    node2) echo "gpus=0 rdma=2 crab=no load=3.00" ;;
    *) exit 255 ;;
esac
"""


def run_scan(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    for name, body in (("sinfo", FAKE_SINFO), ("ssh", FAKE_SSH)):
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        SSH=str(tmp_path / "ssh"),
        SINFO=str(tmp_path / "sinfo"),
        NPLB_REPORT_DIR=str(tmp_path / "reports"),
    )
    return subprocess.run(
        [BASH, str(NPLB / "node_scan.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_scan_lists_every_node_with_partitions_and_probe(tmp_path: Path) -> None:
    result = run_scan(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "VERDICT: OK 2 of 3 nodes answered ssh, 1 with GPUs and RDMA ports up" in out
    line1 = next(line for line in out.splitlines() if line.startswith("node1 "))
    assert line1.split() == ["node1", "gpu,other", "idle", "yes", "8", "16", "yes", "0.10"]
    line3 = next(line for line in out.splitlines() if line.startswith("node3 "))
    assert line3.split() == ["node3", "gpu", "down", "no", "-", "-", "-", "-"]


def test_scan_of_named_hosts_skips_sinfo(tmp_path: Path) -> None:
    result = run_scan(tmp_path, "node3")
    assert result.returncode == 1
    assert "VERDICT: WARN none of 1 nodes answered ssh" in result.stdout
