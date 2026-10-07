"""`remote_path_expr` keeps `~` expanding to $HOME but must not let the rest of the path run
anything: inside double quotes bash still expands `$(...)`, backticks and `$VAR`."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from crab.web.remoteops.crab_cli import RemotePath, build_crab_command, remote_path_expr
from crab.web.store.profiles import Profile


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


def _crab_words(args: list[str], home: Path) -> list[str]:
    """The arguments `crab` would receive from `build_crab_command`, after bash expands them."""
    cmd = build_crab_command(Profile(name="local", transport="local"), args)
    prefix = f"{shlex.quote(sys.executable)} -m crab "
    assert cmd.startswith(prefix)
    return subprocess.run(
        ["bash", "-c", f"printf '%s\\n' {cmd[len(prefix) :]}"],
        capture_output=True,
        text=True,
        timeout=10,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        cwd=home,
    ).stdout.splitlines()


def test_a_non_path_argument_keeps_its_tilde(tmp_path: Path) -> None:
    assert _crab_words(["run", "~/x"], tmp_path) == ["run", "~/x"]


def test_a_path_argument_expands_its_tilde(tmp_path: Path) -> None:
    assert _crab_words(["run", RemotePath("~/x")], tmp_path) == ["run", f"{tmp_path}/x"]


@pytest.mark.parametrize("arg", ["$(touch pwned)", "`touch pwned`", "~/$(touch pwned)", "$HOME"])
def test_a_non_path_argument_stays_inert(tmp_path: Path, arg: str) -> None:
    assert _crab_words(["run", arg], tmp_path) == ["run", arg]
    assert not (tmp_path / "pwned").exists()
