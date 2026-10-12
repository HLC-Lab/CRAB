"""`crab update` moves an install forward safely: a branch checkout fast-forwards, a tag install
moves to the newest tag of its line, dependencies are reinstalled only when pyproject.toml
changed, local edits to tracked files stop it, and the wrappers checkout is pulled or cloned.
All against real throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crab.cli.update import UpdateError, run_update


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
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


@pytest.fixture
def crab(tmp_path: Path):
    bare, work = _origin(tmp_path, "crab", {"pyproject.toml": "deps = 1\n", "a.py": "1\n"})
    install = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(bare), str(install)], check=True)
    return work, install


def _update(install: Path, tmp_path: Path, pip: Pip, wrappers_url: str = "") -> dict:
    return run_update(install, tmp_path / "no-wrappers", wrappers_url, pip)


def test_a_branch_checkout_fast_forwards_without_reinstalling(crab, tmp_path) -> None:
    work, install = crab
    new = _commit(work, "a.py", "2\n")
    pip = Pip()
    result = _update(install, tmp_path, pip)
    assert git(install, "rev-parse", "HEAD") == new
    assert result["crab"]["mode"] == "branch" and result["crab"]["new"] == new[:12]
    assert pip.calls == []


def test_dependencies_are_reinstalled_when_pyproject_changes(crab, tmp_path) -> None:
    work, install = crab
    _commit(work, "pyproject.toml", "deps = 2\n")
    pip = Pip()
    _update(install, tmp_path, pip)
    assert pip.calls == [install]


def test_local_edits_to_tracked_files_stop_it(crab, tmp_path) -> None:
    work, install = crab
    before = git(install, "rev-parse", "HEAD")
    _commit(work, "a.py", "2\n")
    (install / "a.py").write_text("my edit\n")
    with pytest.raises(UpdateError, match="a.py"):
        _update(install, tmp_path, Pip())
    assert git(install, "rev-parse", "HEAD") == before
    assert (install / "a.py").read_text() == "my edit\n"


@pytest.mark.parametrize(
    ("start", "expected"),
    [("v0.2.0", "v0.10.0"), ("v0.2.0+sbatchman", "v0.3.0+sbatchman")],
)
def test_a_tag_install_moves_to_the_newest_tag_of_its_line(crab, tmp_path, start, expected) -> None:
    work, install = crab
    for i, tag in enumerate(
        ["v0.2.0", "v0.2.0+sbatchman", "v0.9.0", "v0.10.0", "v0.3.0+sbatchman"]
    ):
        _commit(work, "a.py", f"{i}\n", tag=tag)
    git(install, "fetch", "-q", "--tags")
    git(install, "checkout", "-q", start)
    result = _update(install, tmp_path, Pip())
    assert result["crab"]["mode"] == "tag"
    assert result["crab"]["new"] == expected
    assert git(install, "describe", "--tags", "--exact-match") == expected


def test_the_wrappers_checkout_is_cloned_when_missing_then_pulled(crab, tmp_path) -> None:
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    wdir = tmp_path / "wrappers-dir"
    first = run_update(install, wdir, str(wbare), Pip())
    assert first["wrappers"]["action"] == "cloned" and (wdir / "README.md").exists()

    new = _commit(wwork, "README.md", "w2\n")
    second = run_update(install, wdir, str(wbare), Pip())
    assert second["wrappers"]["action"] == "updated"
    assert git(wdir, "rev-parse", "HEAD") == new


def test_wrappers_that_are_part_of_the_checkout_are_left_alone(crab, tmp_path) -> None:
    _, install = crab
    inside = install / "wrappers"
    inside.mkdir()
    (inside / "w.py").write_text("x\n")
    result = run_update(install, inside, "unused-url", Pip())
    assert result["wrappers"]["action"] == "skipped"


def test_not_a_git_checkout_is_a_clear_error(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(UpdateError, match="not a git checkout"):
        run_update(plain, tmp_path / "w", "", Pip())


@pytest.mark.parametrize(
    ("branch", "expected"), [("main", "v0.10.0"), ("sbatchman", "v0.3.0+sbatchman")]
)
def test_to_release_moves_a_fresh_clone_to_the_newest_tag_of_its_line(
    crab, tmp_path, branch, expected
) -> None:
    work, _ = crab
    for i, tag in enumerate(["v0.9.0", "v0.10.0", "v0.3.0+sbatchman"]):
        _commit(work, "a.py", f"{i}\n", tag=tag)
    git(work, "push", "-q", "origin", f"main:{branch}")
    fresh = tmp_path / f"fresh-{branch}"
    subprocess.run(
        ["git", "clone", "-q", "--branch", branch, str(tmp_path / "crab.git"), str(fresh)],
        check=True,
    )
    result = run_update(fresh, tmp_path / "w", "", Pip(), to_release=True)
    assert result["crab"]["mode"] == "tag" and result["crab"]["new"] == expected
    assert git(fresh, "describe", "--tags", "--exact-match") == expected


def test_to_release_without_any_tag_stays_on_the_branch(crab, tmp_path) -> None:
    _, install = crab
    result = run_update(install, tmp_path / "w", "", Pip(), to_release=True)
    assert result["crab"]["mode"] == "branch"
    assert git(install, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_leftovers_of_the_old_tracked_wrappers_folder_are_removed_and_cloned_over(
    crab, tmp_path
) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    (inside / "blink" / "__pycache__").mkdir(parents=True)
    (inside / "blink" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (inside / ".pytest_cache").mkdir()
    (inside / ".pytest_cache" / "README.md").write_text("cache\n")
    result = run_update(install, inside, str(wbare), Pip())
    assert result["wrappers"]["action"] == "cloned"
    assert (inside / "README.md").read_text() == "w\n"
    assert (inside / ".git").exists()
    assert not (inside / "blink").exists() and not (inside / ".pytest_cache").exists()


def test_an_empty_wrappers_folder_is_cloned_over(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    inside.mkdir()
    result = run_update(install, inside, str(wbare), Pip())
    assert result["wrappers"]["action"] == "cloned" and (inside / "README.md").exists()


def test_a_wrappers_folder_with_real_files_is_never_touched(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    (inside / "blink" / "__pycache__").mkdir(parents=True)
    (inside / "blink" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (inside / "blink" / "a.py").write_text("mine\n")
    result = run_update(install, inside, str(wbare), Pip())
    assert result["wrappers"]["action"] == "skipped"
    assert (inside / "blink" / "a.py").read_text() == "mine\n"
    assert (inside / "blink" / "__pycache__" / "x.pyc").exists()


def test_the_wrappers_folder_itself_is_kept_when_its_leftovers_are_cleaned(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    (inside / "blink" / "__pycache__").mkdir(parents=True)
    (inside / "blink" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    inode = inside.stat().st_ino
    result = run_update(install, inside, str(wbare), Pip())
    assert result["wrappers"]["action"] == "cloned"
    assert inside.stat().st_ino == inode
    assert (inside / "README.md").exists() and (inside / ".git").exists()


def test_a_symlinked_wrappers_folder_is_skipped_and_left_intact(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    target = tmp_path / "elsewhere"
    (target / "__pycache__").mkdir(parents=True)
    (target / "__pycache__" / "x.pyc").write_bytes(b"\0")
    link = install / "wrappers"
    link.symlink_to(target, target_is_directory=True)
    result = run_update(install, link, str(wbare), Pip())
    assert result["wrappers"]["action"] == "skipped"
    assert link.is_symlink() and (target / "__pycache__" / "x.pyc").exists()


def test_an_unreadable_subfolder_is_a_clear_error_and_nothing_is_deleted(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    (inside / "ok" / "__pycache__").mkdir(parents=True)
    (inside / "ok" / "__pycache__" / "y.pyc").write_bytes(b"\0")
    (inside / "locked").mkdir()
    (inside / "locked").chmod(0o000)
    try:
        with pytest.raises(UpdateError, match="cannot clean the leftovers"):
            run_update(install, inside, str(wbare), Pip())
    finally:
        (inside / "locked").chmod(0o755)
    assert (inside / "ok" / "__pycache__" / "y.pyc").exists()
    assert not (inside / ".git").exists()


def test_a_pycache_folder_holding_anything_but_pyc_files_is_not_a_leftover(crab, tmp_path) -> None:
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _, install = crab
    inside = install / "wrappers"
    (inside / "__pycache__").mkdir(parents=True)
    (inside / "__pycache__" / "notes.txt").write_text("mine\n")
    result = run_update(install, inside, str(wbare), Pip())
    assert result["wrappers"]["action"] == "skipped"
    assert (inside / "__pycache__" / "notes.txt").read_text() == "mine\n"
