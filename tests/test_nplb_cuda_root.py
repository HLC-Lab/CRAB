"""examples/nplb/lib/cuda.sh (nplb_cuda_root): which CUDA folder the lab scripts use by default."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"


def cuda_root(parent: Path) -> str:
    result = subprocess.run(
        [BASH, "-c", f". {NPLB / 'lib' / 'cuda.sh'}; nplb_cuda_root"],
        env={"PATH": "/usr/bin:/bin", "NPLB_CUDA_PARENT": str(parent)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def make(parent: Path, *names: str) -> None:
    for name in names:
        (parent / name).mkdir()


def test_cuda_12_3_wins_when_present(tmp_path: Path) -> None:
    make(tmp_path, "cuda", "cuda-12.3", "cuda-12.8", "cuda-13.3")
    assert cuda_root(tmp_path) == f"{tmp_path}/cuda-12.3"


def test_newest_12_x_when_12_3_is_gone_and_13_is_never_picked(tmp_path: Path) -> None:
    make(tmp_path, "cuda", "cuda-12.8", "cuda-12.10", "cuda-13.3")
    assert cuda_root(tmp_path) == f"{tmp_path}/cuda-12.10"


def test_without_any_12_x_it_names_12_3_so_the_check_says_it_is_missing(tmp_path: Path) -> None:
    make(tmp_path, "cuda", "cuda-13.3")
    assert cuda_root(tmp_path) == f"{tmp_path}/cuda-12.3"
