"""The repo's .gitignore ignores `wrappers` at the repo root whether it is a folder or a symlink
(in the umbrella repo it is a symlink to a sibling crab-wrappers checkout). Real `git` processes
against a copy of the .gitignore in a tmp repo."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GITIGNORE = Path(__file__).resolve().parents[1] / ".gitignore"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tmp git repo holding a copy of the repo's .gitignore."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copy(GITIGNORE, repo / ".gitignore")
    return repo


def _make_symlink(repo: Path) -> None:
    sibling = repo.parent / "crab-wrappers"
    sibling.mkdir()
    (sibling / "x.py").write_text("x\n")
    (repo / "wrappers").symlink_to(sibling, target_is_directory=True)


def _make_directory(repo: Path) -> None:
    (repo / "wrappers").mkdir()
    (repo / "wrappers" / "x.py").write_text("x\n")


def test_symlink_wrappers_is_ignored(repo: Path) -> None:
    _make_symlink(repo)
    assert _git(repo, "check-ignore", "-q", "wrappers").returncode == 0


def test_directory_wrappers_is_ignored(repo: Path) -> None:
    _make_directory(repo)
    assert _git(repo, "check-ignore", "-q", "wrappers").returncode == 0
    assert _git(repo, "check-ignore", "-q", "wrappers/x.py").returncode == 0


def test_status_does_not_list_wrappers(repo: Path) -> None:
    _make_symlink(repo)
    status = _git(repo, "status", "--porcelain")
    assert status.returncode == 0
    assert "wrappers" not in status.stdout
    (repo / "wrappers").unlink()
    _make_directory(repo)
    status = _git(repo, "status", "--porcelain")
    assert status.returncode == 0
    assert "wrappers" not in status.stdout


def test_nested_wrappers_is_not_ignored(repo: Path) -> None:
    (repo / "src" / "wrappers").mkdir(parents=True)
    (repo / "src" / "wrappers" / "x").write_text("x\n")
    assert _git(repo, "check-ignore", "-q", "src/wrappers/x").returncode == 1
    assert _git(repo, "check-ignore", "-q", "src/wrappers").returncode == 1
