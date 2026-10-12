"""The Makefile clones the crab-wrappers repository into wrappers/ when it is missing, between
`pip install -e .` and `crab setup`. Real `make` and `git` processes against a copy of the
Makefile in a tmp dir and a local bare repository: no network."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tmp dir holding a copy of the Makefile."""
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MAKEFILE, project / "Makefile")
    return project


@pytest.fixture
def origin(tmp_path: Path) -> str:
    """A file:// URL of a bare repository with one commit."""
    bare, work = tmp_path / "wrappers.git", tmp_path / "wrappers-work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("w\n")
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*git, "push", "-q", str(bare), "main"], check=True)
    return f"file://{bare}"


def _make(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(project), *args], capture_output=True, text=True, check=False
    )


def test_wrappers_are_cloned_when_missing_and_left_alone_when_present(project, origin) -> None:
    first = _make(project, "_wrappers", f"WRAPPERS_URL={origin}")
    assert first.returncode == 0, first.stderr
    assert (project / "wrappers" / ".git").exists()
    assert (project / "wrappers" / "README.md").read_text() == "w\n"

    (project / "wrappers" / "marker").write_text("keep\n")
    second = _make(project, "_wrappers", "WRAPPERS_URL=file:///nonexistent/never-cloned")
    assert second.returncode == 0, second.stderr
    assert "already present" in second.stdout
    assert (project / "wrappers" / "marker").read_text() == "keep\n"


def test_the_install_clones_wrappers_after_pip_and_before_setup(project) -> None:
    result = _make(project, "-n", "_install_core")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()

    def at(needle: str) -> int:
        return next(i for i, line in enumerate(lines) if needle in line)

    assert at("pip install -e .") < at("git clone") < at("bin/crab setup")


def _installed(project: Path) -> None:
    """Make the project look installed: an executable .venv/bin/crab stub."""
    stub = project / ".venv" / "bin" / "crab"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)


def test_rerunning_make_on_an_installed_checkout_clones_missing_wrappers(project, origin) -> None:
    _installed(project)
    result = _make(project, f"WRAPPERS_URL={origin}")
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert (project / "wrappers" / "README.md").read_text() == "w\n"


def test_a_failed_clone_on_an_installed_checkout_points_to_crab_update(project) -> None:
    _installed(project)
    result = _make(project, "WRAPPERS_URL=file:///nonexistent")
    assert result.returncode != 0
    assert "crab update" in result.stderr
    assert not (project / "wrappers" / "README.md").exists()
