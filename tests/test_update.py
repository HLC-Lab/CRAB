"""`crab update` moves an install forward safely: a branch checkout fast-forwards, a tag install
moves to the newest tag of its line, dependencies are reinstalled only when pyproject.toml
changed, local edits to tracked files stop it, and the wrappers checkout is pulled or cloned.
All against real throwaway git repositories."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from crab import paths
from crab.cli import update
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


# What a checkout always has: the file the wrappers tag is derived from when CRAB has no tag.
CRAB_FILES = {"pyproject.toml": "deps = 1\n", "src/crab/__init__.py": '__version__ = "0.1.0"\n'}


@pytest.fixture
def crab(tmp_path: Path):
    bare, work = _origin(tmp_path, "crab", {**CRAB_FILES, "a.py": "1\n"})
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
    second = run_update(install, wdir, str(wbare), Pip(), head=True)
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
def test_the_default_moves_a_fresh_clone_to_the_newest_tag_of_its_line(
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
    result = run_update(fresh, tmp_path / "w", "", Pip())
    assert result["crab"]["mode"] == "tag" and result["crab"]["new"] == expected
    assert git(fresh, "describe", "--tags", "--exact-match") == expected


def test_the_default_without_any_tag_stays_on_the_branch(crab, tmp_path) -> None:
    _, install = crab
    result = run_update(install, tmp_path / "w", "", Pip())
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


# --- A5: the old `crab` distribution goes before the reinstall ---------------------------------


class FakeRun:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> None:
        self.commands.append(command)


def _fake_pip(monkeypatch, old_dist: bool) -> FakeRun:
    run = FakeRun()
    monkeypatch.setattr(update.subprocess, "run", run)

    def distribution(name: str):
        if name == "crab" and old_dist:
            return object()
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    return run


def test_pip_install_removes_an_old_crab_distribution_first(monkeypatch) -> None:
    run = _fake_pip(monkeypatch, old_dist=True)
    update._pip_install(Path("/repo"))
    assert [c[3] for c in run.commands] == ["uninstall", "install"]
    assert run.commands[0][-2:] == ["-y", "crab"]


def test_pip_install_without_an_old_distribution_only_installs(monkeypatch) -> None:
    run = _fake_pip(monkeypatch, old_dist=False)
    update._pip_install(Path("/repo"))
    assert [c[3] for c in run.commands] == ["install"]


# --- the presets file moved: tell the owner exactly what to do --------------------------------

PRESETS_MOVED = (
    "config/presets.json moved to src/crab/presets.json in this version: copy your edits to "
    "local/presets.json, run `git checkout config/presets.json`, then `crab update` again"
)


def test_an_edited_old_presets_file_gets_the_migration_message(crab, tmp_path) -> None:
    work, install = crab
    (work / "config").mkdir()
    _commit(work, "config/presets.json", "{}\n")
    git(install, "pull", "-q")
    (install / "config" / "presets.json").write_text('{"mine": 1}\n')
    (install / "a.py").write_text("my edit\n")
    with pytest.raises(UpdateError) as caught:
        _update(install, tmp_path, Pip())
    assert str(caught.value) == PRESETS_MOVED


def test_other_edited_files_keep_the_generic_refusal(crab, tmp_path) -> None:
    _, install = crab
    (install / "a.py").write_text("my edit\n")
    with pytest.raises(UpdateError) as caught:
        _update(install, tmp_path, Pip())
    assert "Local edits to tracked files" in str(caught.value)
    assert "moved" not in str(caught.value)


# --- D5: release by default, --head follows the branch -----------------------------------------


def _crab_install(tmp_path: Path, tags: tuple[str, ...] = ("v0.9.0", "v1.0.0")):
    bare, work = _origin(tmp_path, "crab", {**CRAB_FILES, "a.py": "1\n"})
    for i, tag in enumerate(tags):
        _commit(work, "a.py", f"t{i}\n", tag=tag)
    _commit(work, "a.py", "after-tags\n")
    install = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(bare), str(install)], check=True)
    return work, install


@pytest.fixture
def pair(tmp_path):
    """A crab checkout and a wrappers clone, each origin with tags v0.9.0 and v1.0.0 and one
    commit after the last tag. Returns (crab_work, install, wrappers_work, wdir, url)."""
    cwork, install = _crab_install(tmp_path)
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _commit(wwork, "README.md", "w9\n", tag="v0.9.0")
    _commit(wwork, "README.md", "w10\n", tag="v1.0.0")
    _commit(wwork, "README.md", "w-head\n")
    wdir = tmp_path / "wrappers-dir"
    subprocess.run(["git", "clone", "-q", str(wbare), str(wdir)], check=True)
    return cwork, install, wwork, wdir, str(wbare)


def _head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def test_the_default_moves_crab_and_the_wrappers_to_the_release(pair) -> None:
    _, install, wwork, wdir, url = pair
    result = run_update(install, wdir, url, Pip())
    assert result["crab"]["mode"] == "tag" and result["crab"]["new"] == "v1.0.0"
    assert result["wrappers"]["action"] == "updated"
    assert _head_of(wdir) == git(wwork, "rev-parse", "v1.0.0^{commit}")
    assert _head_of(wdir) != _head_of(wwork)
    assert result["crab"]["ref"] == result["wrappers"]["ref"] == "release"


def test_the_wrappers_tag_is_the_crab_version_after_the_update(tmp_path) -> None:
    cwork, _ = _crab_install(tmp_path, tags=("v0.9.0", "v1.0.0+sbatchman", "v2.0.0"))
    git(cwork, "push", "-q", "origin", "main:sbatchman")
    fresh = tmp_path / "fresh"
    subprocess.run(
        ["git", "clone", "-q", "--branch", "sbatchman", str(tmp_path / "crab.git"), str(fresh)],
        check=True,
    )
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _commit(wwork, "README.md", "w1\n", tag="v1.0.0")
    _commit(wwork, "README.md", "w2\n", tag="v2.0.0")
    wdir = tmp_path / "wrappers-dir"
    result = run_update(fresh, wdir, str(wbare), Pip())
    assert result["crab"]["new"] == "v1.0.0+sbatchman"
    assert _head_of(wdir) == git(wwork, "rev-parse", "v1.0.0^{commit}")


def test_a_release_update_with_no_tag_takes_the_version_from_the_tree(tmp_path) -> None:
    work, _ = _crab_install(tmp_path, tags=())
    _commit(work, "src/crab/__init__.py", '__version__ = "3.1.4"\n')
    install = tmp_path / "install2"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "crab.git"), str(install)], check=True)
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _commit(wwork, "README.md", "w1\n", tag="v3.1.4")
    _commit(wwork, "README.md", "w2\n")
    wdir = tmp_path / "wrappers-dir"
    subprocess.run(["git", "clone", "-q", str(wbare), str(wdir)], check=True)
    result = run_update(install, wdir, str(wbare), Pip())
    assert result["crab"]["mode"] == "branch"
    assert _head_of(wdir) == git(wwork, "rev-parse", "v3.1.4^{commit}")


def test_a_missing_wrappers_tag_leaves_the_wrappers_where_they_are(pair) -> None:
    cwork, install, _, wdir, url = pair
    _commit(cwork, "a.py", "again\n", tag="v1.1.0")
    before = _head_of(wdir)
    result = run_update(install, wdir, url, Pip())
    assert result["crab"]["new"] == "v1.1.0"
    wrappers = result["wrappers"]
    assert wrappers["action"] == "kept"
    assert wrappers["detail"] == f"no wrappers tag v1.1.0 yet, left at {before[:12]}"
    assert wrappers["old"] == wrappers["new"] == before[:12]
    assert _head_of(wdir) == before


def test_a_missing_wrappers_tag_on_a_fresh_clone_keeps_the_branch_head(pair, tmp_path) -> None:
    cwork, install, wwork, _, url = pair
    _commit(cwork, "a.py", "again\n", tag="v1.1.0")
    wdir = tmp_path / "fresh-wrappers"
    result = run_update(install, wdir, url, Pip())
    assert result["wrappers"]["action"] == "cloned"
    assert result["wrappers"]["detail"].startswith("no wrappers tag v1.1.0 yet")
    assert _head_of(wdir) == _head_of(wwork)


def test_head_follows_the_branch_for_crab_and_the_wrappers(pair) -> None:
    cwork, install, wwork, wdir, url = pair
    git(wdir, "checkout", "-q", "v0.9.0")
    result = run_update(install, wdir, url, Pip(), head=True)
    assert result["crab"]["mode"] == "branch" and _head_of(install) == _head_of(cwork)
    assert result["crab"]["ref"] == result["wrappers"]["ref"] == "head"
    assert result["wrappers"]["action"] == "updated"
    assert git(wdir, "symbolic-ref", "--short", "HEAD") == "main"
    assert _head_of(wdir) == _head_of(wwork)


def test_head_after_a_release_update_leaves_the_detached_wrappers_on_the_branch(pair) -> None:
    _, install, wwork, wdir, url = pair
    run_update(install, wdir, url, Pip())
    assert git(wdir, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    git(install, "checkout", "-q", "main")
    result = run_update(install, wdir, url, Pip(), head=True)
    assert result["wrappers"]["action"] == "updated"
    assert _head_of(wdir) == _head_of(wwork)


def test_json_keeps_every_key_and_adds_ref(pair) -> None:
    from crab.cli.contract import CONTRACT_SCHEMA

    _, install, _, wdir, url = pair
    result = run_update(install, wdir, url, Pip())
    assert result["schema"] == CONTRACT_SCHEMA
    assert set(result["crab"]) == {"mode", "old", "new", "changed", "reinstalled", "ref"}
    assert set(result["wrappers"]) == {"dir", "action", "old", "new", "ref"}
    git(install, "checkout", "-q", "main")
    head = run_update(install, wdir, url, Pip(), head=True)
    assert head["crab"]["ref"] == "head" and head["wrappers"]["ref"] == "head"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    update.register(parser.add_subparsers())
    return parser


def test_to_release_is_an_alias_of_the_default_and_head_is_a_flag() -> None:
    assert _parser().parse_args(["update", "--to-release"]).head is False
    assert _parser().parse_args(["update"]).head is False
    assert _parser().parse_args(["update", "--head"]).head is True


def test_head_and_to_release_exclude_each_other(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        _parser().parse_args(["update", "--head", "--to-release"])
    assert caught.value.code == 2


# --- D5b: a pip install updates the wrappers only ----------------------------------------------


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """Pretend CRAB is pip-installed; records every git call made on the CRAB root."""
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    root = tmp_path / "home"
    root.mkdir()
    calls: list[tuple[Path, tuple[str, ...]]] = []
    real_git = update.git

    def spy(repo: Path, *args: str, **kwargs: Any) -> str:
        calls.append((repo, args))
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(update, "git", spy)
    return root, calls


def test_installed_never_runs_git_on_the_root_and_updates_the_wrappers(installed, tmp_path) -> None:
    import crab

    root, calls = installed
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _commit(wwork, "README.md", f"{crab.__version__}\n", tag=f"v{crab.__version__}")
    _commit(wwork, "README.md", "after\n")
    wdir = root / "wrappers"
    pip = Pip()
    result = run_update(root, wdir, str(wbare), pip)
    assert calls == [] and pip.calls == []
    assert result["crab"] == {
        "mode": "pip",
        "old": crab.__version__,
        "new": crab.__version__,
        "changed": False,
        "reinstalled": False,
        "hint": "pipx upgrade crab-hpc",
        "ref": "release",
    }
    assert result["wrappers"]["action"] == "cloned"
    assert _head_of(wdir) == git(wwork, "rev-parse", f"v{crab.__version__}^{{commit}}")
    head = run_update(root, wdir, str(wbare), pip, head=True)
    assert head["crab"]["mode"] == "pip" and head["crab"]["ref"] == "head"
    assert head["wrappers"]["action"] == "updated" and _head_of(wdir) == _head_of(wwork)
    assert calls == []


def test_installed_without_a_wrappers_tag_keeps_the_wrappers(installed, tmp_path) -> None:
    root, _ = installed
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    wdir = root / "wrappers"
    subprocess.run(["git", "clone", "-q", str(wbare), str(wdir)], check=True)
    result = run_update(root, wdir, str(wbare), Pip())
    assert result["wrappers"]["action"] == "kept"


def test_the_human_text_for_a_pip_install_points_to_pipx(
    installed, tmp_path, monkeypatch, capsys
) -> None:
    root, _ = installed
    wbare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    monkeypatch.setattr(update, "WRAPPERS_REPO_URL", str(wbare))
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(root / "wrappers"))
    monkeypatch.setattr("crab.core.wrapper_paths._CRAB_ROOT", str(root))
    update.handle_update(argparse.Namespace(head=True, to_release=False, json=False))
    out = capsys.readouterr().out
    assert "pipx upgrade crab-hpc" in out and "wrappers: cloned" in out
    update.handle_update(argparse.Namespace(head=True, to_release=False, json=True))
    assert json.loads(capsys.readouterr().out)["crab"]["mode"] == "pip"


# --- review fixes: detached --head, wrappers edits, partial failure, kept line ------------------


def _point_handle_update_at(monkeypatch, root: Path, wdir: Path, url: str) -> None:
    monkeypatch.setattr(update, "WRAPPERS_REPO_URL", url)
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(wdir))
    monkeypatch.setattr("crab.core.wrapper_paths._CRAB_ROOT", str(root))


def test_head_on_a_detached_crab_checkout_is_refused_before_anything_moves(pair) -> None:
    _, install, _, wdir, url = pair
    run_update(install, wdir, url, Pip())
    crab_before, wrappers_before = _head_of(install), _head_of(wdir)
    with pytest.raises(UpdateError) as caught:
        run_update(install, wdir, url, Pip(), head=True)
    assert str(caught.value) == (
        f"CRAB is on release v1.0.0, not a branch: run `git checkout <branch>` in {install}, "
        "then `crab update --head`"
    )
    assert _head_of(install) == crab_before and _head_of(wdir) == wrappers_before


def test_head_on_a_detached_checkout_stops_handle_update_with_exit_1(
    pair, monkeypatch, capsys
) -> None:
    _, install, _, wdir, url = pair
    run_update(install, wdir, url, Pip())
    _point_handle_update_at(monkeypatch, install, wdir, url)
    with pytest.raises(SystemExit) as caught:
        update.handle_update(argparse.Namespace(head=True, to_release=False, json=False))
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("crab: update stopped: CRAB is on release v1.0.0")


def test_a_wrappers_clone_with_local_edits_is_refused_before_crab_moves(pair) -> None:
    _, install, _, wdir, url = pair
    crab_before = _head_of(install)
    (wdir / "README.md").write_text("my edit\n")
    with pytest.raises(UpdateError, match="local edits.*README.md"):
        run_update(install, wdir, url, Pip())
    assert _head_of(install) == crab_before
    assert git(install, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (wdir / "README.md").read_text() == "my edit\n"


def test_a_wrappers_failure_after_crab_moved_says_crab_moved(pair, tmp_path) -> None:
    _, install, _, _, _ = pair
    with pytest.raises(UpdateError) as caught:
        run_update(install, tmp_path / "no-wrappers", str(tmp_path / "missing.git"), Pip())
    assert str(caught.value).startswith("CRAB is now at v1.0.0; the wrappers step failed: ")
    assert git(install, "describe", "--tags", "--exact-match") == "v1.0.0"


def test_a_wrappers_failure_when_crab_did_not_move_has_no_prefix(crab, tmp_path) -> None:
    _, install = crab
    with pytest.raises(UpdateError) as caught:
        run_update(install, tmp_path / "no-wrappers", str(tmp_path / "missing.git"), Pip())
    assert "CRAB is now at" not in str(caught.value)


def test_the_human_text_shows_the_kept_wrappers_line(pair, monkeypatch, capsys) -> None:
    cwork, install, _, wdir, url = pair
    _commit(cwork, "a.py", "again\n", tag="v1.1.0")
    before = _head_of(wdir)
    _point_handle_update_at(monkeypatch, install, wdir, url)
    update.handle_update(argparse.Namespace(head=False, to_release=False, json=False))
    out = capsys.readouterr().out
    assert f"wrappers: kept (no wrappers tag v1.1.0 yet, left at {before[:12]})" in out
