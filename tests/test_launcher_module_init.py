"""The per-app launcher script initialises the `module` command from the first init script found.

The script is generated through `run_job` with `subprocess.Popen` mocked (the app never starts),
then run by a real `bash` against fake init scripts under `tmp_path`.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from crab.core.process import manager


class _Job:
    id_num = 7
    node_list = ["node1"]

    def __init__(self, run_dir: Path):
        self.run_dir = str(run_dir)
        self.process = None

    def run_app(self) -> str:
        return "echo app"

    def set_process(self, process) -> None:
        self.process = process


def _fake_init(path: Path, marker: str) -> Path:
    """An init script that exports a marker and defines a `module` function."""
    path.write_text(
        f'export INIT_MARKER="{marker}"\n'
        'module() { echo "module $*"; }\n'
        'export INIT_COUNT="${INIT_COUNT:-}x"\n'
    )
    return path


def _generate_script(
    tmp_path: Path, candidates: tuple[str, ...], pre_commands: list[str] | None
) -> Path:
    """Run `run_job` with Popen mocked and return the generated launcher script path."""
    wlmanager = MagicMock()
    wlmanager.run_job.return_value = 'echo "marker=$INIT_MARKER count=$INIT_COUNT"'
    job = _Job(tmp_path)
    with (
        patch.object(manager, "MODULE_INIT_SCRIPTS", candidates),
        patch.object(manager.subprocess, "Popen") as popen,
    ):
        popen.return_value.stdout.readline.return_value = b""
        manager.run_job(job, wlmanager, ppn=1, logger=MagicMock(), pre_commands=pre_commands)
    return tmp_path / ".wrappers" / "app_7.sh"


def _run_bash(script: Path, tmp_path: Path, extra_env: dict[str, str] | None = None):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=30
    )


def test_fake_init_is_sourced_and_the_hook_can_use_module(tmp_path):
    init = _fake_init(tmp_path / "lmod_init", "from-lmod")
    script = _generate_script(tmp_path, (str(init),), ["module load x"])
    result = _run_bash(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "module load x" in result.stdout
    assert "marker=from-lmod" in result.stdout


def test_only_the_first_existing_candidate_is_sourced(tmp_path):
    missing = tmp_path / "missing.sh"
    first = _fake_init(tmp_path / "first.sh", "first")
    second = _fake_init(tmp_path / "second.sh", "second")
    script = _generate_script(tmp_path, (str(missing), str(first), str(second)), None)
    result = _run_bash(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "marker=first count=x\n" in result.stdout


def test_lmod_pkg_init_is_a_last_candidate(tmp_path):
    pkg = tmp_path / "lmodpkg" / "init"
    pkg.mkdir(parents=True)
    _fake_init(pkg / "bash", "from-lmod-pkg")
    script = _generate_script(tmp_path, (str(tmp_path / "missing.sh"),), ["module load y"])
    result = _run_bash(script, tmp_path, {"LMOD_PKG": str(tmp_path / "lmodpkg")})
    assert result.returncode == 0, result.stderr
    assert "marker=from-lmod-pkg" in result.stdout
    assert "module load y" in result.stdout


def test_no_candidate_still_runs_the_hooks_without_errors(tmp_path):
    script = _generate_script(tmp_path, (str(tmp_path / "missing.sh"),), ["echo hook-ran"])
    result = _run_bash(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "hook-ran" in result.stdout
    assert "marker= count=" in result.stdout


def test_paths_are_shell_quoted(tmp_path):
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    init = _fake_init(spaced / "init.sh", "spaced")
    script = _generate_script(tmp_path, (str(init),), None)
    result = _run_bash(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "marker=spaced" in result.stdout


def test_default_candidates_are_absolute_and_start_with_modules_sh():
    assert manager.MODULE_INIT_SCRIPTS[0] == "/etc/profile.d/modules.sh"
    assert all(path.startswith("/") for path in manager.MODULE_INIT_SCRIPTS)
