"""`crab update` and `crab wrappers pull|status` report their slow steps (git network calls, pip).

A recording reporter stands in for the terminal; git runs against real throwaway repositories,
or a fake `subprocess.run` where the test needs a credential in a URL or a failing command."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from crab.cli import gitops, progress_render, update, wrappers_cmd
from crab.cli.gitops import GitError
from crab.cli.progress import Stage, stage, use_reporter
from crab.cli.update import run_update


class Recorder:
    def __init__(self) -> None:
        self.begun: list[Stage] = []
        self.events: list[tuple[str, str, bool | None]] = []

    def begin(self, s: Stage) -> None:
        self.begun.append(s)
        self.events.append(("begin", s.stage, None))

    def end(self, stage_id: str, ok: bool) -> None:
        self.events.append(("end", stage_id, ok))

    def ids(self) -> list[str]:
        return [s.stage for s in self.begun]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _origin(tmp: Path, name: str, files: dict[str, str]) -> tuple[Path, Path]:
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


def _commit(work: Path, rel: str, text: str) -> None:
    (work / rel).write_text(text)
    git(work, "add", rel)
    git(work, "commit", "-q", "-m", f"change {rel}")
    git(work, "push", "-q", "origin", "main")


@pytest.fixture
def pair(tmp_path: Path):
    """A crab checkout and a wrappers clone, each one commit behind its origin (no tags)."""
    cbare, cwork = _origin(
        tmp_path,
        "crab",
        {"pyproject.toml": "deps = 1\n", "src/crab/__init__.py": '__version__ = "0.1.0"\n'},
    )
    install = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(cbare), str(install)], check=True)
    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    wdir = tmp_path / "wrappers-dir"
    subprocess.run(["git", "clone", "-q", str(wbare), str(wdir)], check=True)
    _commit(cwork, "a.py", "1\n")
    _commit(wwork, "README.md", "w2\n")
    return install, wdir, cbare, wbare


class NoPip:
    def __call__(self, root: Path) -> None:
        pass


def test_a_head_update_reports_one_pull_per_repository_and_ends_each(pair) -> None:
    install, wdir, cbare, wbare = pair
    rec = Recorder()
    with use_reporter(rec):
        run_update(install, wdir, str(wbare), NoPip(), head=True)
    assert rec.ids() == ["git-pull", "git-pull"]
    assert rec.begun[0].waiting_for == f"git pull from {cbare}"
    assert rec.begun[1].waiting_for == f"git pull from {wbare}"
    assert all(s.timeout_s == 120 for s in rec.begun)
    # each stage ends before the next one begins, and ok
    assert [e[0] for e in rec.events] == ["begin", "end", "begin", "end"]
    assert all(e[2] for e in rec.events if e[0] == "end")


def test_a_release_update_reports_fetches(pair) -> None:
    install, wdir, _, wbare = pair
    rec = Recorder()
    with use_reporter(rec):
        run_update(install, wdir, str(wbare), NoPip())
    assert rec.ids()[0] == "git-fetch"
    assert set(rec.ids()) <= {"git-fetch", "git-pull"}
    assert all(e[2] for e in rec.events if e[0] == "end")


def test_local_git_commands_report_nothing(pair) -> None:
    install, *_ = pair
    rec = Recorder()
    with use_reporter(rec):
        git_head = gitops.git(install, "rev-parse", "HEAD")
    assert git_head and rec.events == []


class FakeRun:
    """Stands in for subprocess.run inside gitops: answers `remote get-url`, fails the rest."""

    def __init__(self, origin: str, fail: bool = True) -> None:
        self.origin, self.fail, self.calls = origin, fail, []

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        if "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.origin + "\n", "")
        return subprocess.CompletedProcess(cmd, 1 if self.fail else 0, "", "fatal: no route")


def test_credentials_in_an_origin_url_never_reach_the_stage_text(monkeypatch, tmp_path) -> None:
    secret_url = "https://user:s3cret@example.org/org/repo.git"
    monkeypatch.setattr(gitops.subprocess, "run", FakeRun(secret_url))
    rec = Recorder()
    with use_reporter(rec), pytest.raises(GitError):
        gitops.git(tmp_path, "fetch", "-q", "--tags", "origin")
    assert rec.begun[0].waiting_for == "git fetch from https://example.org/org/repo.git"
    assert "s3cret" not in repr(rec.begun) and "user:" not in repr(rec.begun)
    assert rec.events[-1] == ("end", "git-fetch", False)


def test_a_clone_names_the_url_it_clones_without_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gitops.subprocess, "run", FakeRun("unused", fail=False))
    rec = Recorder()
    with use_reporter(rec):
        gitops.git(tmp_path, "clone", "-q", "https://u:pw@example.org/w.git", str(tmp_path / "d"))
    assert rec.ids() == ["git-clone"]
    assert rec.begun[0].waiting_for == "git clone from https://example.org/w.git"


def test_an_unreadable_origin_still_gets_a_stage(monkeypatch, tmp_path) -> None:
    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess:
        code = 1 if "get-url" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "", "")

    monkeypatch.setattr(gitops.subprocess, "run", run)
    rec = Recorder()
    with use_reporter(rec):
        gitops.git(tmp_path, "ls-remote", "origin", "HEAD")
    assert rec.ids() == ["git-ls-remote"]
    assert rec.begun[0].waiting_for == "git ls-remote from origin"


class FakeSubprocess:
    def __init__(self, fail: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.fail = fail

    def __call__(self, command: list[str], **_: Any) -> None:
        self.commands.append(command)
        if self.fail:
            raise subprocess.CalledProcessError(1, command)


def _no_old_dist(monkeypatch) -> None:
    def distribution(name: str):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)


def test_pip_install_is_one_stage(monkeypatch) -> None:
    run = FakeSubprocess()
    monkeypatch.setattr(update.subprocess, "run", run)
    _no_old_dist(monkeypatch)
    rec = Recorder()
    with use_reporter(rec):
        update._pip_install(Path("/repo"))
    assert rec.ids() == ["pip-install"]
    assert rec.begun[0].waiting_for.startswith("pip install")
    assert len(run.commands) == 1 and rec.events[-1] == ("end", "pip-install", True)


def test_a_failing_pip_install_ends_its_stage_not_ok(monkeypatch) -> None:
    monkeypatch.setattr(update.subprocess, "run", FakeSubprocess(fail=True))
    _no_old_dist(monkeypatch)
    rec = Recorder()
    with use_reporter(rec), pytest.raises(subprocess.CalledProcessError):
        update._pip_install(Path("/repo"))
    assert rec.events[-1] == ("end", "pip-install", False)


_RESULT = {
    "schema": 1,
    "crab": {
        "mode": "pip",
        "old": "1.0.0",
        "new": "1.0.0",
        "changed": False,
        "reinstalled": False,
        "hint": update.PIP_HINT,
        "ref": "head",
    },
    "wrappers": {"action": "up to date", "old": "abc", "new": "abc"},
}


def _slow_update(*_: Any, **__: Any) -> dict[str, Any]:
    with stage("git-pull", "Pulling updates", "git pull from example.org"):
        return _RESULT


def test_json_update_is_silent_on_stderr_and_stdout_stays_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update, "run_update", _slow_update)
    update.handle_update(argparse.Namespace(head=True, to_release=False, json=True))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["crab"]["mode"] == "pip"


def test_a_plain_update_writes_the_stage_line_to_stderr_not_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update, "run_update", _slow_update)
    update.handle_update(argparse.Namespace(head=True, to_release=False, json=False))
    captured = capsys.readouterr()
    assert "Pulling updates: waiting for git pull from example.org" in captured.err
    assert "Pulling updates" not in captured.out and "CRAB (pip install)" in captured.out


@pytest.fixture
def recording(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(progress_render, "for_args", lambda args: rec)
    return rec


@pytest.fixture
def clone(tmp_path: Path, monkeypatch):
    bare, _ = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    wdir = tmp_path / "wrappers-dir"
    subprocess.run(["git", "clone", "-q", str(bare), str(wdir)], check=True)
    monkeypatch.setattr(wrappers_cmd, "_wrappers_folder", lambda: (tmp_path, wdir))
    return wdir, bare


def test_wrappers_status_reports_the_upstream_check(clone, recording, capsys) -> None:
    _, bare = clone
    wrappers_cmd.handle_wrappers_status(argparse.Namespace(json=True))
    assert recording.ids() == ["git-ls-remote"]
    assert recording.begun[0].waiting_for == f"git ls-remote from {bare}"
    assert recording.begun[0].timeout_s == 20
    assert json.loads(capsys.readouterr().out)["state"] == "up to date"


def test_wrappers_pull_reports_the_pull(clone, recording, capsys, monkeypatch) -> None:
    from crab.cli import wrappers_clone

    monkeypatch.setattr(wrappers_clone, "WRAPPERS_REPO_URL", str(clone[1]))
    wrappers_cmd.handle_wrappers_pull(argparse.Namespace(json=False))
    assert recording.ids() == ["git-pull"]
    assert "wrappers: up to date" in capsys.readouterr().out


def test_json_wrappers_commands_are_silent_on_stderr(clone, capsys) -> None:
    wrappers_cmd.handle_wrappers_status(argparse.Namespace(json=True))
    captured = capsys.readouterr()
    assert captured.err == "" and json.loads(captured.out)["clone"] is True


def test_a_plain_wrappers_status_shows_the_wait_on_stderr(clone, capsys) -> None:
    wrappers_cmd.handle_wrappers_status(argparse.Namespace(json=False))
    captured = capsys.readouterr()
    assert "waiting for git ls-remote from" in captured.err
    assert "waiting for" not in captured.out


def _fake_first_clone(announce) -> None:
    with stage("git-clone", "Cloning", "git clone from https://example.org/w.git"):
        announce("Cloning the CRAB wrappers (first use)")


def test_the_first_use_clone_of_a_wrappers_command_shows_its_stage(
    recording, monkeypatch, capsys
) -> None:
    from crab.cli import wrappers_clone

    monkeypatch.setattr(wrappers_clone, "ensure_wrappers_clone", _fake_first_clone)
    wrappers_cmd._ensure_wrappers(argparse.Namespace(json=False))
    assert recording.ids() == ["git-clone"]
    assert "first use" in capsys.readouterr().out


def test_the_first_use_clone_during_setup_shows_its_stage(recording, monkeypatch) -> None:
    from crab.cli import main, wrappers_clone

    monkeypatch.setattr(wrappers_clone, "ensure_wrappers_clone", _fake_first_clone)
    monkeypatch.setattr("crab.setup.wizard.run", lambda: None)
    main.handle_setup(argparse.Namespace())
    assert recording.ids() == ["git-clone"]
    assert recording.events[-1] == ("end", "git-clone", True)


def test_a_json_wrappers_command_skips_the_first_use_clone_and_its_stage(
    recording, monkeypatch
) -> None:
    from crab.cli import wrappers_clone

    monkeypatch.setattr(wrappers_clone, "ensure_wrappers_clone", _fake_first_clone)
    wrappers_cmd._ensure_wrappers(argparse.Namespace(json=True))
    assert recording.events == []
