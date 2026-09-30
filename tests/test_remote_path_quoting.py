"""`remote_path_expr` keeps `~` expanding to $HOME but must not let the rest of the path run
anything: inside double quotes bash still expands `$(...)`, backticks and `$VAR`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crab.web.remoteops.crab_cli import remote_path_expr


def _bash_echo(expr: str, home: Path) -> str:
    return subprocess.run(
        ["bash", "-c", f"printf %s {expr}"],
        capture_output=True,
        text=True,
        timeout=10,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        cwd=home,
    ).stdout


@pytest.mark.parametrize(
    "rest",
    ["CRAB", "my dir/CRAB", "a$(touch pwned)b", "a`touch pwned`b", "a$USER", 'a"b', "a'b", "a\\b"],
)
def test_tilde_paths_expand_home_and_nothing_else(tmp_path: Path, rest: str) -> None:
    assert _bash_echo(remote_path_expr(f"~/{rest}"), tmp_path) == f"{tmp_path}/{rest}"
    assert not (tmp_path / "pwned").exists()


def test_bare_tilde_and_plain_paths(tmp_path: Path) -> None:
    assert _bash_echo(remote_path_expr("~"), tmp_path) == str(tmp_path)
    assert _bash_echo(remote_path_expr("/opt/$(x)"), tmp_path) == "/opt/$(x)"
