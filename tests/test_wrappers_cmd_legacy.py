"""`crab parse` on a wrapper that returns the list-per-metric shape (removed in CRAB 1.0)
exits 2 with the reason, instead of converting it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LEGACY_WRAPPER = """from crab.wrappers.base import base


class app(base):
    metadata = [{"name": "lat", "unit": "us", "conv": True}]

    def read_data(self):
        return [[float(x) for x in self.stdout.split()]]
"""


def test_parse_of_a_legacy_wrapper_exits_2_with_the_reason(tmp_path: Path) -> None:
    (tmp_path / "oldapp.py").write_text(LEGACY_WRAPPER)
    (tmp_path / "stdout.txt").write_text("1.5 2.5\n")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from crab.cli.main import cli_router; cli_router()",
            "parse",
            str(tmp_path / "oldapp.py"),
            str(tmp_path / "stdout.txt"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 2
    assert "wrapper 'oldapp'" in out.stderr
    assert "removed in CRAB 1.0" in out.stderr
    assert '[{"lat": 1.0}]' in out.stderr
    assert "docs/extending/wrappers.md" in out.stderr
    assert out.stdout == ""
