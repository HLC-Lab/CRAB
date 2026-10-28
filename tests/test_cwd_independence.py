"""A `crab` command answers the same from any folder (plan 112).

Each read-only `--json` command runs through the installed console script, as a user would, from
the repository root, from an unrelated empty folder and from a folder that no longer exists.
Commands that need no cluster and no network only: `nodes` finds no `sinfo` and says so, with
exit status 0 everywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
CRAB = Path(sys.executable).parent / "crab"

READ_ONLY_COMMANDS = [
    ["info", "--json"],
    ["list-benchmarks", "--json"],
    ["wrappers", "list", "--json"],
    ["history", "--json"],
    ["nodes", "--json"],
]


@pytest.fixture
def crab_env(tmp_path: Path) -> dict[str, str]:
    """The environment of a child `crab`: the user folders sit under `tmp_path`, no CRAB_* variable."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRAB_")}
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    for name, folder in (
        ("XDG_CONFIG_HOME", ".config"),
        ("XDG_DATA_HOME", ".local/share"),
        ("XDG_STATE_HOME", ".local/state"),
        ("XDG_CACHE_HOME", ".cache"),
    ):
        env[name] = str(home / folder)
    return env


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CRAB), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


def _run_from_deleted_folder(
    args: list[str], scratch: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run `crab` with a current folder that was removed (Popen cannot start in one: bash does)."""
    doomed = scratch / "doomed"
    doomed.mkdir()
    script = 'cd "$1" && rmdir "$1" && exec "$2" "${@:3}"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(doomed), str(CRAB), *args],
        cwd=scratch,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("args", READ_ONLY_COMMANDS, ids=[" ".join(a) for a in READ_ONLY_COMMANDS])
def test_a_read_only_command_answers_the_same_from_any_folder(
    args: list[str], tmp_path: Path, crab_env: dict[str, str]
) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    runs = {
        "repo root": _run(args, REPO, crab_env),
        "unrelated folder": _run(args, unrelated, crab_env),
        "deleted folder": _run_from_deleted_folder(args, scratch, crab_env),
    }

    for where, result in runs.items():
        assert result.returncode == 0, f"{where}: exit {result.returncode}: {result.stderr}"
        assert "Traceback" not in result.stderr, where
    answers = {where: json.loads(result.stdout) for where, result in runs.items()}
    assert answers["unrelated folder"] == answers["repo root"]
    assert answers["deleted folder"] == answers["repo root"]


def test_a_relative_crab_home_stops_every_command(tmp_path: Path, crab_env: dict[str, str]) -> None:
    result = _run(["info", "--json"], tmp_path, {**crab_env, "CRAB_HOME": "rel/home"})

    lines = result.stderr.splitlines()
    assert result.returncode == 2
    assert len(lines) == 1 and lines[0].startswith("crab: ")
    assert "CRAB_HOME" in lines[0]
    assert result.stdout == ""


def _write_config(folder: Path, app: dict[str, Any]) -> Path:
    config = {
        "global_options": {"numnodes": "1", "ppn": "1"},
        "experiments": {"e1": {"apps": {"0": app}}},
    }
    path = folder / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_validate_refuses_a_relative_binary_and_names_the_field(
    tmp_path: Path, crab_env: dict[str, str]
) -> None:
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir()
    (wrappers / "a.py").write_text("class app: pass\n")
    config = _write_config(tmp_path, {"path": "a.py", "binary": "bin/xhpl"})

    result = _run(
        ["validate", str(config)], tmp_path, {**crab_env, "CRAB_PATH_WRAPPERS": str(wrappers)}
    )

    assert result.returncode != 0
    assert "binary" in result.stderr
    assert "bin/xhpl" in result.stderr


def test_validate_does_not_look_for_a_wrapper_in_the_current_folder(
    tmp_path: Path, crab_env: dict[str, str]
) -> None:
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir()
    (wrappers / "other.py").write_text("class app: pass\n")
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "only-in-the-current-folder.py").write_text("class app: pass\n")
    config = _write_config(tmp_path, {"path": "only-in-the-current-folder.py"})

    result = _run(
        ["validate", "--json", str(config)],
        caller,
        {**crab_env, "CRAB_PATH_WRAPPERS": str(wrappers)},
    )

    report = json.loads(result.stdout)
    assert result.returncode != 0
    assert report["ok"] is False
    assert any("only-in-the-current-folder.py not found" in error for error in report["errors"])
