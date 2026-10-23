"""`crab wrappers status` and `crab wrappers pull`: the shared crab-wrappers clone against a real
bare origin in tmp_path. Status reads only (it never fetches); pull fast-forwards or clones, and
refuses a clone with edited tracked files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import crab.cli.gitops as gitops
import crab.cli.wrappers_clone as wrappers_clone
import crab.core.wrapper_paths as wp
from crab.cli.gitops import GitError


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(work: Path, rel: str, text: str) -> str:
    (work / rel).write_text(text)
    git(work, "add", rel)
    git(work, "commit", "-q", "-m", f"change {rel}")
    return git(work, "rev-parse", "HEAD")


class Setup:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bare = tmp / "origin.git"
        self.work = tmp / "origin-work"
        self.clone = tmp / "wrappers"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        (self.work / "app.py").write_text("1\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "-q", "-m", "init")
        git(self.work, "remote", "add", "origin", str(self.bare))
        git(self.work, "push", "-q", "origin", "main")
        self.first = git(self.work, "rev-parse", "HEAD")

    def make_clone(self) -> None:
        subprocess.run(["git", "clone", "-q", str(self.bare), str(self.clone)], check=True)

    def push_new(self, text: str = "2\n") -> str:
        sha = _commit(self.work, "app.py", text)
        git(self.work, "push", "-q", "origin", "main")
        return sha


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    s = Setup(tmp_path)
    monkeypatch.setattr(wrappers_clone, "WRAPPERS_REPO_URL", str(s.bare))
    return s


def _crab(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    from crab.cli.main import cli_router

    monkeypatch.setattr(sys, "argv", ["crab", *args])
    try:
        cli_router()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def _status(monkeypatch, capsys) -> dict:
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "status", "--json")
    assert code == 0
    return json.loads(out)


# ---- status --------------------------------------------------------------------------------


def test_status_up_to_date(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    data = _status(monkeypatch, capsys)
    assert data["schema"]
    assert data["clone"] is True
    assert data["dir"] == str(setup.clone)
    assert data["commit"] == setup.first
    assert data["subject"] == "init"
    assert data["branch"] == "main"
    assert data["modified"] == []
    assert data["upstream"] == {"url": str(setup.bare), "head": setup.first, "error": None}
    assert (data["behind"], data["ahead"]) == (0, 0)
    assert data["state"] == "up to date"


def test_status_behind_without_fetch_changes_nothing(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    new = setup.push_new()
    head = git(setup.clone, "rev-parse", "HEAD")
    refs = git(setup.clone, "for-each-ref")
    data = _status(monkeypatch, capsys)
    assert data["state"] == "behind"
    assert data["behind"] is None and data["ahead"] is None
    assert data["upstream"]["head"] == new
    assert git(setup.clone, "rev-parse", "HEAD") == head
    assert git(setup.clone, "for-each-ref") == refs


def test_status_behind_after_fetch_counts(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    setup.push_new()
    git(setup.clone, "fetch", "-q")
    data = _status(monkeypatch, capsys)
    assert data["state"] == "behind"
    assert (data["behind"], data["ahead"]) == (1, 0)


def test_status_ahead_and_diverged(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    _commit(setup.clone, "local.py", "x\n")
    data = _status(monkeypatch, capsys)
    assert data["state"] == "ahead"
    assert (data["ahead"], data["behind"]) == (1, 0)

    setup.push_new()
    git(setup.clone, "fetch", "-q")
    data = _status(monkeypatch, capsys)
    assert data["state"] == "diverged"
    assert (data["ahead"], data["behind"]) == (1, 1)


def test_status_lists_tracked_modifications_only(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    (setup.clone / "app.py").write_text("edited\n")
    (setup.clone / "untracked.py").write_text("u\n")
    data = _status(monkeypatch, capsys)
    assert data["modified"] == ["app.py"]


def test_status_unreachable_origin(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "remote", "set-url", "origin", str(setup.tmp / "nowhere.git"))
    data = _status(monkeypatch, capsys)
    assert data["upstream"]["head"] is None
    assert isinstance(data["upstream"]["error"], str) and data["upstream"]["error"]
    assert data["state"] == "unknown"
    assert data["behind"] is None and data["ahead"] is None
    assert data["commit"] == setup.first
    assert data["modified"] == []


def test_status_subject_is_the_head_commits_subject_line(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    (setup.clone / "more.py").write_text("m\n")
    git(setup.clone, "add", "more.py")
    git(setup.clone, "commit", "-q", "-m", "blink: new subject", "-m", "A body line.")
    data = _status(monkeypatch, capsys)
    assert data["subject"] == "blink: new subject"


def test_status_folder_missing(setup, monkeypatch, capsys) -> None:
    data = _status(monkeypatch, capsys)
    assert data["clone"] is False
    assert "missing" in data["detail"]
    assert data["commit"] is None and data["branch"] is None
    assert data["subject"] is None
    assert data["modified"] == []
    assert data["behind"] is None and data["ahead"] is None
    assert data["state"] == "unknown"


def test_status_plain_folder_is_not_a_clone(setup, monkeypatch, capsys) -> None:
    setup.clone.mkdir()
    (setup.clone / "stuff.py").write_text("s\n")
    data = _status(monkeypatch, capsys)
    assert data["clone"] is False
    assert "not a git clone" in data["detail"]
    assert data["commit"] is None
    assert data["subject"] is None
    assert data["state"] == "unknown"


def test_status_human_output(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    (setup.clone / "app.py").write_text("edited\n")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "status")
    assert code == 0
    assert setup.first[:12] in out
    assert "init" in out.splitlines()[0]
    assert "up to date" in out
    assert "app.py" in out
    assert len(out.strip().splitlines()) <= 3


# ---- pull ----------------------------------------------------------------------------------


def test_pull_fast_forwards_then_is_up_to_date(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    new = setup.push_new()
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["schema"]
    assert data["action"] == "updated"
    assert data["old"] == setup.first[:12] and data["new"] == new[:12]
    assert git(setup.clone, "rev-parse", "HEAD") == new

    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 0
    assert json.loads(out)["action"] == "up to date"


def test_pull_refuses_a_modified_clone(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    setup.push_new()
    (setup.clone / "app.py").write_text("edited\n")
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 1
    assert out == ""
    assert err.startswith("crab: wrappers pull stopped:")
    assert "app.py" in err
    assert git(setup.clone, "rev-parse", "HEAD") == setup.first
    assert (setup.clone / "app.py").read_text() == "edited\n"


def test_pull_clones_when_missing(setup, monkeypatch, capsys) -> None:
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 0
    assert json.loads(out)["action"] == "cloned"
    assert git(setup.clone, "rev-parse", "HEAD") == setup.first
    assert git(setup.clone, "remote", "get-url", "origin") == str(setup.bare)


def test_pull_failure_is_reported(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    _commit(setup.clone, "local.py", "x\n")
    setup.push_new()
    git(setup.clone, "fetch", "-q")
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 1
    assert out == ""
    assert err.startswith("crab: wrappers pull stopped:")


# ---- review follow-ups ---------------------------------------------------------------------


def test_status_hides_credentials_in_the_origin_url(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "remote", "set-url", "origin", "https://u:tok@127.0.0.1:1/x.git")
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "status", "--json")
    assert code == 0
    assert "tok" not in out and "tok" not in err
    assert json.loads(out)["upstream"]["url"] == "https://127.0.0.1:1/x.git"


def test_status_keeps_scp_style_urls(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "remote", "set-url", "origin", "git@127.0.0.1:x/y.git")
    assert _status(monkeypatch, capsys)["upstream"]["url"] == "git@127.0.0.1:x/y.git"


def test_status_does_not_write_the_index(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    tracked = setup.clone / "app.py"
    os.utime(tracked, (1_000_000_000, 1_000_000_000))  # stat changes, content does not
    index = setup.clone / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    data = _status(monkeypatch, capsys)
    assert data["modified"] == []
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def test_status_modified_paths_are_plain(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    (setup.clone / "sp ace.py").write_text("s\n")
    (setup.clone / "naïve.py").write_text("n\n")
    git(setup.clone, "add", "sp ace.py", "naïve.py")
    git(setup.clone, "commit", "-q", "-m", "more files")
    git(setup.clone, "mv", "app.py", "renamed app.py")
    (setup.clone / "sp ace.py").write_text("edited\n")
    (setup.clone / "naïve.py").write_text("edited\n")
    data = _status(monkeypatch, capsys)
    assert {"sp ace.py", "naïve.py", "renamed app.py"} <= set(data["modified"])
    assert not any('"' in path or "->" in path for path in data["modified"])


def test_pull_refusal_does_not_touch_the_network(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "remote", "set-url", "origin", str(setup.tmp / "nowhere.git"))
    (setup.clone / "app.py").write_text("edited\n")

    def no_network(*args, **kwargs):
        raise AssertionError("pull's pre-check must not ask GitHub")

    monkeypatch.setattr(wrappers_clone, "_upstream_head", no_network)
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 1
    assert out == ""
    assert err.startswith("crab: wrappers pull stopped:") and "app.py" in err


def test_status_survives_a_failing_rev_list(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    real_git = wrappers_clone.git

    def flaky(repo, *args, **kwargs):
        if args[0] == "rev-list":
            raise GitError("git rev-list failed: boom")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(wrappers_clone, "git", flaky)
    data = _status(monkeypatch, capsys)
    assert data["state"] == "unknown"
    assert data["behind"] is None and data["ahead"] is None
    assert "boom" in data["upstream"]["error"]


def test_ls_remote_runs_without_prompts(setup, monkeypatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="abc\trefs/heads/main\n", stderr="")

    monkeypatch.setattr(gitops.subprocess, "run", fake_run)
    assert wrappers_clone._upstream_head(setup.tmp, "main") == "abc"
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert seen["env"]["PATH"] == os.environ["PATH"]


def test_status_of_a_repo_without_commits(setup, monkeypatch, capsys) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(setup.clone)], check=True)
    data = _status(monkeypatch, capsys)
    assert data["clone"] is True
    assert data["detail"] == "no commits yet"
    assert data["commit"] is None
    assert data["subject"] is None
    assert data["state"] == "unknown"


def test_status_detached_head_follows_origin_head(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "checkout", "-q", "--detach")
    data = _status(monkeypatch, capsys)
    assert data["branch"] is None
    assert data["upstream"]["head"] == setup.first
    assert data["state"] == "up to date"


def test_status_when_origin_lost_the_branch(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.bare, "update-ref", "-d", "refs/heads/main")
    data = _status(monkeypatch, capsys)
    assert data["state"] == "unknown"
    assert data["upstream"]["head"] is None
    assert "refs/heads/main" in data["upstream"]["error"]


def test_pull_failure_leaves_the_clone_alone(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    local = _commit(setup.clone, "local.py", "x\n")
    setup.push_new()
    git(setup.clone, "fetch", "-q")
    _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert git(setup.clone, "rev-parse", "HEAD") == local
    assert git(setup.clone, "status", "--porcelain") == ""
    assert (setup.clone / "app.py").read_text() == "1\n"


def test_pull_on_a_detached_head_stops(setup, monkeypatch, capsys) -> None:
    setup.make_clone()
    git(setup.clone, "checkout", "-q", "--detach")
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "pull", "--json")
    assert code == 1
    assert out == ""
    assert err.startswith("crab: wrappers pull stopped:")


# --- update_wrappers_clone: release tag and --head modes ---------------------------------------


def _tag_origin(setup: Setup, tag: str) -> str:
    git(setup.work, "tag", tag)
    git(setup.work, "push", "-q", "origin", tag)
    return git(setup.work, "rev-parse", f"{tag}^{{commit}}")


def test_a_tag_moves_the_clone_to_it_and_reports_the_release_ref(setup, tmp_path) -> None:
    tagged = _tag_origin(setup, "v1.0.0")
    setup.push_new()
    setup.make_clone()
    result = wrappers_clone.update_wrappers_clone(tmp_path, setup.clone, str(setup.bare), "v1.0.0")
    assert (result["action"], result["ref"]) == ("updated", "release")
    assert git(setup.clone, "rev-parse", "HEAD") == tagged
    again = wrappers_clone.update_wrappers_clone(tmp_path, setup.clone, str(setup.bare), "v1.0.0")
    assert again["action"] == "up to date"


def test_a_missing_tag_keeps_the_clone_where_it_is(setup, tmp_path) -> None:
    setup.make_clone()
    before = git(setup.clone, "rev-parse", "HEAD")
    result = wrappers_clone.update_wrappers_clone(tmp_path, setup.clone, str(setup.bare), "v9.9.9")
    assert result["action"] == "kept"
    assert result["detail"] == f"no wrappers tag v9.9.9 yet, left at {before[:12]}"
    assert git(setup.clone, "rev-parse", "HEAD") == before


def test_rejoin_branch_returns_a_detached_clone_to_the_default_branch(setup, tmp_path) -> None:
    setup.make_clone()
    git(setup.clone, "checkout", "-q", "--detach")
    newest = setup.push_new()
    result = wrappers_clone.update_wrappers_clone(
        tmp_path, setup.clone, str(setup.bare), rejoin_branch=True
    )
    assert (result["action"], result["ref"]) == ("updated", "head")
    assert git(setup.clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(setup.clone, "rev-parse", "HEAD") == newest


# ---- first-use clone (ensure_wrappers_clone) -----------------------------------------------


@pytest.fixture
def installed(setup, monkeypatch):
    """An installed CRAB (not a checkout) whose wrappers dir is `setup.clone`, with git calls
    recorded: `calls` holds the argument list of every git command the clone helpers run."""
    import crab.paths as crab_paths

    monkeypatch.setattr(crab_paths, "is_checkout", lambda: False)
    calls: list[tuple[str, ...]] = []
    real = wrappers_clone.git

    def recording(repo, *args, **kwargs):
        calls.append(args)
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(wrappers_clone, "git", recording)
    setup.calls = calls
    return setup


def _clones(setup) -> int:
    return sum(1 for args in setup.calls if args and args[0] == "clone")


def test_ensure_clones_a_missing_dir_once_and_announces_it(installed) -> None:
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert _clones(installed) == 1
    assert git(installed.clone, "rev-parse", "HEAD") == installed.first
    assert len(said) == 1
    assert str(installed.clone) in said[0] and "first use" in said[0]


def test_ensure_clones_over_leftovers_only(installed) -> None:
    (installed.clone / "__pycache__").mkdir(parents=True)
    (installed.clone / "__pycache__" / "x.pyc").write_bytes(b"")
    (installed.clone / ".pytest_cache").mkdir()
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert _clones(installed) == 1
    assert (installed.clone / "app.py").is_file()
    assert len(said) == 1


def test_ensure_lands_on_the_release_tag_when_there_is_one(installed, monkeypatch) -> None:
    import crab

    git(installed.work, "tag", "v9.9.9")
    git(installed.work, "push", "-q", "origin", "v9.9.9")
    installed.push_new()  # the default branch moves past the tag
    monkeypatch.setattr(crab, "__version__", "9.9.9")
    wrappers_clone.ensure_wrappers_clone(lambda line: None)
    assert git(installed.clone, "rev-parse", "HEAD") == installed.first


def test_ensure_follows_the_default_branch_without_a_matching_tag(installed, monkeypatch) -> None:
    import crab

    new = installed.push_new()
    monkeypatch.setattr(crab, "__version__", "9.9.9")
    wrappers_clone.ensure_wrappers_clone(lambda line: None)
    assert git(installed.clone, "rev-parse", "HEAD") == new


def test_ensure_does_nothing_in_a_checkout(installed, monkeypatch) -> None:
    import crab.paths as crab_paths

    monkeypatch.setattr(crab_paths, "is_checkout", lambda: True)
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert installed.calls == [] and said == []
    assert not installed.clone.exists()


def test_ensure_does_nothing_when_wrappers_path_is_set(installed, monkeypatch) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(installed.tmp / "mine"))
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert installed.calls == [] and said == []
    assert not installed.clone.exists()


def test_ensure_does_nothing_for_an_existing_clone(installed) -> None:
    installed.make_clone()
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert installed.calls == [] and said == []


def test_ensure_leaves_a_folder_with_real_files_alone(installed) -> None:
    installed.clone.mkdir()
    (installed.clone / "mine.py").write_text("x = 1\n")
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert installed.calls == [] and said == []
    assert (installed.clone / "mine.py").read_text() == "x = 1\n"


def test_ensure_without_a_git_binary_prints_the_hint(installed, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(installed.tmp / "no-bin"))
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert not installed.clone.exists()
    assert said[0].startswith("Cloning the CRAB wrappers")
    assert any(wp.missing_wrappers_hint() in line for line in said[1:])


def test_ensure_with_a_failing_clone_prints_the_hint(installed, monkeypatch) -> None:
    monkeypatch.setattr(wrappers_clone, "WRAPPERS_REPO_URL", str(installed.tmp / "nowhere.git"))
    said: list[str] = []
    wrappers_clone.ensure_wrappers_clone(said.append)
    assert not (installed.clone / ".git").exists()
    assert any(wp.missing_wrappers_hint() in line for line in said[1:])


# ---- call sites ----------------------------------------------------------------------------


@pytest.fixture
def ensure_spy(monkeypatch) -> list[object]:
    calls: list[object] = []
    monkeypatch.setattr(wrappers_clone, "ensure_wrappers_clone", lambda announce: calls.append(1))
    return calls


def _run_config(tmp_path: Path) -> str:
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    return str(path)


def test_execute_orchestrator_ensures_the_wrappers_once(
    ensure_spy, tmp_path, monkeypatch, shared_presets
) -> None:
    from unittest.mock import patch

    from crab.cli.orchestrator import execute_orchestrator

    shared_presets({"local": {"scheduler": "local"}})
    monkeypatch.chdir(tmp_path)
    with (
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(_run_config(tmp_path), "local")
    assert ensure_spy == [1]


def test_execute_orchestrator_skips_it_for_json(
    ensure_spy, tmp_path, monkeypatch, shared_presets
) -> None:
    from unittest.mock import patch

    from crab.cli.orchestrator import execute_orchestrator

    shared_presets({"local": {"scheduler": "local"}})
    monkeypatch.chdir(tmp_path)
    with (
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(_run_config(tmp_path), "local", as_json=True)
    assert ensure_spy == []


def test_the_worker_never_ensures_the_wrappers(ensure_spy, tmp_path) -> None:
    from crab.cli.orchestrator import execute_worker

    with pytest.raises(SystemExit):
        execute_worker(str(tmp_path))  # no config.json: fails right after start-up
    assert ensure_spy == []


def test_wrappers_list_test_new_ensure_the_wrappers(ensure_spy, setup, monkeypatch, capsys) -> None:
    _crab(monkeypatch, capsys, "wrappers", "list")
    _crab(monkeypatch, capsys, "wrappers", "test")
    _crab(monkeypatch, capsys, "wrappers", "new", "verifyapp", "--dir", str(setup.tmp / "new"))
    assert ensure_spy == [1, 1, 1]


def test_wrappers_list_json_does_not_ensure(ensure_spy, setup, monkeypatch, capsys) -> None:
    _crab(monkeypatch, capsys, "wrappers", "list", "--json")
    assert ensure_spy == []


def test_setup_ensures_the_wrappers(ensure_spy, monkeypatch) -> None:
    import crab.setup.wizard as wizard
    from crab.cli.main import handle_setup

    monkeypatch.setattr(wizard, "run", lambda: None)
    handle_setup(None)
    assert ensure_spy == [1]
