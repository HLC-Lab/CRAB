"""Inside a git superproject (CRAB checked out as a submodule), `crab update` and
`crab wrappers pull` refuse before changing anything: the umbrella's `git submodule update` is
the only updater. All against real throwaway git repositories."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from crab.cli import update, wrappers_clone, wrappers_cmd
from crab.cli.gitops import superproject_root
from crab.cli.update import UpdateError, run_update

CRAB_FILES = {"pyproject.toml": "deps = 1\n", "src/crab/__init__.py": '__version__ = "0.1.0"\n'}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "protocol.file.allow=always",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _origin(tmp: Path, name: str, files: dict[str, str]) -> tuple[Path, Path]:
    """A bare origin plus a working repo that pushes to it."""
    bare, work = tmp / f"{name}.git", tmp / f"{name}-work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    return bare, work


def _commit(work: Path, rel: str, text: str, tag: str | None = None) -> str:
    (work / rel).write_text(text)
    git(work, "add", rel)
    git(work, "commit", "-q", "-m", f"change {rel}")
    if tag:
        git(work, "tag", tag)
    git(work, "push", "-q", "--tags", "origin", "main")
    return git(work, "rev-parse", "HEAD")


class Pip:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, root: Path) -> None:
        self.calls.append(root)


def _superproject(tmp: Path, bare: Path, name: str = "crab") -> tuple[Path, Path]:
    """A superproject with `bare` added as the submodule `name`; returns (superproject, submodule)."""
    sup = tmp / "umbrella"
    subprocess.run(["git", "init", "-q", "-b", "main", str(sup)], check=True)
    git(sup, "submodule", "add", "-q", str(bare), name)
    git(sup, "commit", "-q", "-m", "add submodule")
    return sup, sup / name


@pytest.fixture
def setup(tmp_path: Path):
    bare, work = _origin(tmp_path, "crab", {**CRAB_FILES, "a.py": "1\n"})
    sup, sub = _superproject(tmp_path, bare)
    return work, sup, sub


def test_superproject_root_of_a_submodule(setup) -> None:
    _, sup, sub = setup
    found = superproject_root(sub)
    assert found is not None and found.resolve() == sup.resolve()


def test_superproject_root_is_none_for_a_standalone_clone(tmp_path) -> None:
    bare, _ = _origin(tmp_path, "crab", CRAB_FILES)
    clone = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    assert superproject_root(clone) is None


def test_superproject_root_is_none_for_a_repo_nested_in_an_unrelated_repo(tmp_path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    subprocess.run(["git", "init", "-q", str(inner)], check=True)
    assert superproject_root(inner) is None


def test_superproject_root_is_none_outside_a_repo(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert superproject_root(plain) is None
    assert superproject_root(tmp_path / "missing") is None


@pytest.mark.parametrize("head", [False, True])
def test_check_crab_refuses_a_submodule(setup, head) -> None:
    _, sup, sub = setup
    with pytest.raises(UpdateError) as excinfo:
        update._check_crab(sub, head=head)
    message = str(excinfo.value)
    assert "is a submodule of" in message
    assert str(sup.resolve()) in message
    assert "git submodule update --init" in message


@pytest.mark.parametrize("head", [False, True])
def test_run_update_in_a_submodule_changes_nothing(setup, tmp_path, head) -> None:
    work, sup, sub = setup
    _commit(work, "a.py", "2\n", tag="v0.2.0")
    _commit(work, "pyproject.toml", "deps = 2\n", tag="v0.3.0")
    before = git(sub, "rev-parse", "HEAD")
    pip = Pip()
    with pytest.raises(UpdateError, match="is a submodule of"):
        run_update(sub, tmp_path / "no-wrappers", "", pip, head=head)
    assert git(sub, "rev-parse", "HEAD") == before
    assert git(sup, "status", "--porcelain") == ""
    assert pip.calls == []


def test_check_wrappers_refuses_a_submodule_clone(tmp_path) -> None:
    bare, _ = _origin(tmp_path, "wrappers", {"w.json": "{}\n"})
    sup, sub = _superproject(tmp_path, bare, name="wrappers")
    with pytest.raises(UpdateError) as excinfo:
        update._check_wrappers(sub)
    message = str(excinfo.value)
    assert f"The wrappers clone {sub}" in message
    assert "is a submodule of" in message
    assert str(sup.resolve()) in message


def test_check_wrappers_passes_a_standalone_clone_and_a_missing_folder(tmp_path) -> None:
    bare, _ = _origin(tmp_path, "wrappers", {"w.json": "{}\n"})
    clone = tmp_path / "wrappers-clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    update._check_wrappers(clone)
    update._check_wrappers(tmp_path / "missing")


def test_wrappers_pull_refuses_a_submodule_before_looking_at_edits(
    tmp_path, monkeypatch, capsys
) -> None:
    bare, _ = _origin(tmp_path, "wrappers", {"w.json": "{}\n"})
    sup, sub = _superproject(tmp_path, bare, name="wrappers")
    monkeypatch.setattr(wrappers_cmd, "_wrappers_folder", lambda: (tmp_path, sub))

    def no_edit_check(_: Path) -> list[str]:
        raise AssertionError("the superproject check must come before the local-edits check")

    monkeypatch.setattr(wrappers_clone, "modified_files", no_edit_check)
    with pytest.raises(SystemExit) as excinfo:
        wrappers_cmd.handle_wrappers_pull(argparse.Namespace(json=True))
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("crab: wrappers pull stopped:")
    assert "is a submodule of" in err and str(sup.resolve()) in err
