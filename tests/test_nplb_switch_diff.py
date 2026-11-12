"""examples/nplb/switch_diff.py (`nplb net diff`): what the odd leaves have or lack against the others."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
COMMON = "sysname X\ninterface 400GE1/0/1\n description n1\n mtu 4200\n#\nreturn\n"


def write(folder: Path, name: str, extra: str = "") -> None:
    (folder / f"{name}.cfg").write_text(COMMON + extra, encoding="utf-8")


def run_diff(folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NPLB / "switch_diff.py"), str(folder)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_lines_only_the_odd_leaves_have_or_lack_are_listed(tmp_path: Path) -> None:
    for name in ("Leaf1", "Leaf2", "Leaf5"):
        write(tmp_path, name, "qos dscp-map 26\n")
    for name in ("Leaf3", "Leaf4"):
        write(tmp_path, name, "traffic-filter udp 4791 deny\n")
    write(tmp_path, "Spine1", "something spine only\n")
    result = run_diff(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (
        "VERDICT: DIFF Leaf3/Leaf4 vs Leaf1/Leaf2/Leaf5: 1 lines missing, 1 extra" in result.stdout
    )
    assert "qos dscp-map N" in result.stdout
    assert "traffic-filter udp N deny" in result.stdout
    assert "spine only" not in result.stdout
    assert "interface" not in result.stdout


def test_equal_configs_say_same_and_a_missing_group_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "Leaf1")
    write(tmp_path, "Leaf3")
    same = run_diff(tmp_path)
    assert same.returncode == 0
    assert "VERDICT: SAME" in same.stdout
    (tmp_path / "Leaf1.cfg").unlink()
    broken = run_diff(tmp_path)
    assert broken.returncode == 2
    assert "need configs" in broken.stderr
