"""A module's PYTHONPATH precedes the venv's site-packages in sys.path, so the venv's pip imports
the module's pip and breaks. The Makefile unexports PYTHONPATH and PYTHONHOME for every recipe.
Real `make` processes against a copy of the Makefile in a tmp dir: no network."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tmp dir with a copy of the Makefile and probe.mk, which adds a target printing the
    two variables as a recipe sees them."""
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MAKEFILE, project / "Makefile")
    (project / "probe.mk").write_text(
        "include Makefile\n"
        "\n"
        "showenv:\n"
        '\t@echo "PYTHONPATH=[$$PYTHONPATH] PYTHONHOME=[$$PYTHONHOME]"\n'
    )
    return project


def _make(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(project), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_recipes_do_not_see_an_inherited_pythonpath_or_pythonhome(project) -> None:
    env = {**os.environ, "PYTHONPATH": "/poison", "PYTHONHOME": "/poison-home"}
    result = _make(project, env, "-f", "probe.mk", "showenv")
    assert result.returncode == 0, result.stderr
    assert "PYTHONPATH=[] PYTHONHOME=[]" in result.stdout


def test_the_venv_recipe_is_still_there(project) -> None:
    result = _make(project, dict(os.environ), "-n", "venv")
    assert result.returncode == 0, result.stderr
    assert "python3 -m venv" in result.stdout


def test_recipes_run_when_pythonpath_and_pythonhome_are_unset(project) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    result = _make(project, env, "-f", "probe.mk", "showenv")
    assert result.returncode == 0, result.stderr
    assert "PYTHONPATH=[] PYTHONHOME=[]" in result.stdout
