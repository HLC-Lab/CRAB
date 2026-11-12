"""examples/nplb/blink_try.sh (`nplb build try`): one Blink run by hand on two nodes, report from its output."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# a fake mpirun: fails (exit 134, a message) when -gpu is among its arguments, else prints one line
FAKE_MPIRUN = """#!/bin/bash
for arg in "$@"; do
    if [[ $arg == -gpu ]]; then
        echo "cuda init failed: libcudart.so.12 not found"
        exit 134
    fi
    if [[ $arg == UCX_TLS=rc_mlx5,self ]]; then
        echo "rc fatal error"
        exit 134
    fi
    if [[ $arg == UCX_NET_DEVICES=mlx5_2:1 ]]; then
        echo "ud_ep.c:296 Fatal: UD endpoint to <no debug data>: unhandled timeout error"
        echo "unrelated noise line"
        exit 134
    fi
done
echo "alltoall ok"
"""


def run_try(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    lab = tmp_path / "lab"
    (lab / "ompi-cuda" / "bin").mkdir(parents=True)
    (lab / "blink-gpu" / "build-cuda" / "bin").mkdir(parents=True)
    mpirun = lab / "ompi-cuda" / "bin" / "mpirun"
    mpirun.write_text(FAKE_MPIRUN, encoding="utf-8")
    mpirun.chmod(0o755)
    binary = lab / "blink-gpu" / "build-cuda" / "bin" / "alltoall_comm_only"
    binary.write_text("#!/bin/bash\n", encoding="utf-8")
    binary.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK")}
    env["TRY_NICS"] = "mlx5_0 mlx5_2"
    env["TRY_TLS"] = "tcp,self rc_mlx5,self"
    env.update(
        NPLB_LAB=str(lab), NPLB_REPORT_DIR=str(tmp_path / "reports"), CUDA_ROOT=str(tmp_path)
    )
    return subprocess.run(
        [BASH, str(NPLB / "blink_try.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_gpu_failure_shows_the_message_and_the_exit_code(tmp_path: Path) -> None:
    result = run_try(tmp_path, "nodeA", "nodeB")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: FAIL gpu tls-rc_mlx5 bound16 nic-mlx5_2" in result.stdout
    assert "FAIL bound16: exit 134" in result.stdout
    assert "OK cpu: exit 0" in result.stdout
    assert "FAIL gpu: exit 134" in result.stdout
    assert "libcudart.so.12 not found" in result.stdout
    assert "OK tls-tcp: exit 0" in result.stdout
    assert "FAIL tls-rc_mlx5: exit 134" in result.stdout
    assert "OK nic-mlx5_0: exit 0" in result.stdout
    assert "FAIL nic-mlx5_2: exit 134" in result.stdout
    assert "unhandled timeout error" in result.stdout
    assert "unrelated noise line" not in result.stdout


def test_one_host_is_a_usage_error(tmp_path: Path) -> None:
    result = run_try(tmp_path, "nodeA")
    assert result.returncode == 2
    assert "usage: nplb build try" in result.stderr
