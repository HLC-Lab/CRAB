"""`crab wrappers show RELPATH [--json]`: one wrapper described for the dashboard.

Drives the real CLI. Git cases build throwaway repositories in tmp_path with a fixed identity, so
the owner's git config is neither read for it nor written.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import crab.core.wrapper_paths as wp
import crab.setup.memory as mem
from crab.cli.contract import CONTRACT_SCHEMA

DOC = '"""Measures t. Checked with app 1.2."""\n\n'

BODY = """from crab.wrappers.base import base


class app(base):
    executable = "onpath"
    metadata = [
        {"name": "t", "unit": "s", "conv": True},
        {"name": "ok", "unit": "", "conv": False, "type": "bool", "role": "check"},
    ]

    def read_data(self):
        return []
"""


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    monkeypatch.setattr(mem, "ENV_DIR", str(tmp_path / "local" / "receipts"))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(tmp_path / "legacy"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "onpath"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}/usr/bin:/bin")
    return tmp_path


def _wrapper(root: Path, rel: str, doc: str = "", folder: str = "wrappers") -> Path:
    path = root / folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc + BODY)
    return path


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _repo(folder: Path, origin: str | None = None, commit: bool = True) -> str | None:
    """Make `folder` a git repo; commit everything in it; return the HEAD sha."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder)], check=True)
    if origin:
        _git(folder, "remote", "add", "origin", origin)
    if not commit:
        return None
    _git(folder, "add", ".")
    _git(folder, "commit", "-q", "-m", "init")
    return _git(folder, "rev-parse", "HEAD")


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


def _show(monkeypatch, capsys, rel: str) -> dict:
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "show", rel, "--json")
    assert code == 0, err
    return json.loads(out)


def test_show_reports_docstring_metrics_and_binary(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py", doc=DOC)
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["schema"] == CONTRACT_SCHEMA
    assert data["relpath"] == "app/w.py"
    assert data["folder"] == str(root / "wrappers")
    assert data["loadable"] is True
    assert data["description"] == {
        "text": "Measures t. Checked with app 1.2.",
        "source": "docstring",
        "path": "app/w.py",
    }
    assert data["metrics"] == [
        {"name": "t", "unit": "s", "role": None, "conv": True},
        {"name": "ok", "unit": "", "role": "check", "conv": False},
    ]
    assert data["executable"] == "onpath"
    assert data["binary"] == {"status": "path", "path": str(root / "bin" / "onpath")}


def test_show_a_metric_without_a_conv_key_is_not_a_target(root, monkeypatch, capsys) -> None:
    path = _wrapper(root, "app/w.py")
    path.write_text(BODY.replace(', "conv": True', "").replace(', "conv": False', ""))
    data = _show(monkeypatch, capsys, "app/w.py")
    assert [m["conv"] for m in data["metrics"]] == [False, False]


def test_show_falls_back_to_the_nearest_readme_paragraph(root, monkeypatch, capsys) -> None:
    _wrapper(root, "suite/group/w.py")
    suite_readme = root / "wrappers" / "suite" / "README.md"
    suite_readme.write_text("# Suite\n\nFirst paragraph\nwraps here.\n\nSecond paragraph.\n")
    (root / "wrappers" / "README.md").write_text("# Repo\n\nThe whole repository.\n")

    data = _show(monkeypatch, capsys, "suite/group/w.py")
    assert data["description"] == {
        "text": "First paragraph wraps here.",
        "source": "readme",
        "path": "suite/README.md",
    }

    # A README in the wrapper's own folder wins over one higher up.
    (root / "wrappers" / "suite" / "group" / "README.md").write_text("Group notes.\n")
    data = _show(monkeypatch, capsys, "suite/group/w.py")
    assert data["description"]["text"] == "Group notes."
    assert data["description"]["path"] == "suite/group/README.md"


def test_show_readme_skips_headings_and_badges(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    (root / "wrappers" / "app" / "README.md").write_text(
        "# App\n\n[![ci](x)](y)\n<img src='z'>\n\nReal text.\n"
    )
    assert _show(monkeypatch, capsys, "app/w.py")["description"]["text"] == "Real text."


def test_show_never_uses_the_search_folders_own_readme(root, monkeypatch, capsys) -> None:
    _wrapper(root, "w.py")
    (root / "wrappers" / "README.md").write_text("# Repo\n\nThe whole repository.\n")
    assert _show(monkeypatch, capsys, "w.py")["description"] == {
        "text": None,
        "source": None,
        "path": None,
    }


def test_show_without_docstring_or_readme(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["description"] == {"text": None, "source": None, "path": None}


def test_show_a_wrapper_that_cannot_load(root, monkeypatch, capsys) -> None:
    path = root / "wrappers" / "app" / "broken.py"
    path.parent.mkdir(parents=True)
    path.write_text(DOC + "raise ImportError('boom')\n")
    data = _show(monkeypatch, capsys, "app/broken.py")
    assert data["loadable"] is False
    assert "boom" in data["error"]
    assert data["description"]["text"] == "Measures t. Checked with app 1.2."
    assert data["description"]["source"] == "docstring"
    assert data["metrics"] == []
    assert data["binary"]["status"] == "unknown"


def test_show_local_wrappers_shadow_the_shared_folder(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    _wrapper(root, "app/w.py", folder="local/wrappers")
    assert _show(monkeypatch, capsys, "app/w.py")["folder"] == str(root / "local" / "wrappers")


def test_show_old_flat_path_resolves_to_the_moved_file(root, monkeypatch, capsys) -> None:
    _wrapper(root, "blink/alltoall/a2a.py")
    data = _show(monkeypatch, capsys, "blink/a2a.py")
    assert data["relpath"] == "blink/alltoall/a2a.py"
    assert data["moved_from"] == "blink/a2a.py"
    direct = _show(monkeypatch, capsys, "blink/alltoall/a2a.py")
    assert direct["moved_from"] is None


def test_show_unknown_wrapper_fails(root, monkeypatch, capsys) -> None:
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "show", "nope/x.py", "--json")
    assert code == 2
    assert out == ""
    assert err.startswith("crab: wrapper not found: nope/x.py")


@pytest.mark.parametrize("bad", ["/abs/path.py", "../x.py", "app/../../x.py"])
def test_show_rejects_paths_outside_the_wrappers_folders(root, monkeypatch, capsys, bad) -> None:
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "show", bad, "--json")
    assert code == 2
    assert out == ""
    assert err.startswith("crab: show takes a path relative to the wrappers folders")


ORIGINS = [
    "https://github.com/O/R.git",
    "https://github.com/O/R",
    "git@github.com:O/R.git",
    "ssh://git@github.com/O/R.git",
]


@pytest.mark.parametrize("origin", ORIGINS)
def test_show_github_url_at_the_clone_commit(root, monkeypatch, capsys, origin) -> None:
    _wrapper(root, "app/w.py")
    sha = _repo(root / "wrappers", origin=origin)
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["source"] == {
        "commit": sha,
        "url": f"https://github.com/O/R/blob/{sha}/app/w.py",
    }


def test_show_url_is_relative_to_the_git_toplevel(root, monkeypatch, capsys) -> None:
    # A CRAB_PATH_WRAPPERS folder inside a clone: the URL path starts at the clone's root.
    _wrapper(root, "app/w.py", folder="clone/sub/wrappers")
    sha = _repo(root / "clone", origin="https://github.com/O/R.git")
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(root / "clone" / "sub" / "wrappers"))
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["source"]["url"] == f"https://github.com/O/R/blob/{sha}/sub/wrappers/app/w.py"


def test_show_no_github_url_when_not_applicable(root, monkeypatch, capsys) -> None:
    none = {"commit": None, "url": None}
    _wrapper(root, "app/w.py")

    # (a) not a git repo
    assert _show(monkeypatch, capsys, "app/w.py")["source"] == none

    # (b) non-GitHub origin
    sha = _repo(root / "wrappers", origin="https://gitlab.com/O/R.git")
    assert _show(monkeypatch, capsys, "app/w.py")["source"] == {"commit": sha, "url": None}


def test_show_reports_no_commit_for_a_file_that_is_not_in_head(root, monkeypatch, capsys) -> None:
    none = {"commit": None, "url": None}
    _wrapper(root, "app/w.py")
    _repo(root / "wrappers", origin="https://github.com/O/R.git")

    # Untracked.
    _wrapper(root, "app/new.py")
    assert _show(monkeypatch, capsys, "app/new.py")["source"] == none

    # Staged but never committed: the commit does not hold it.
    _wrapper(root, "app/staged.py")
    _git(root / "wrappers", "add", "app/staged.py")
    assert _show(monkeypatch, capsys, "app/staged.py")["source"] == none


def test_show_a_git_ignored_wrapper_does_not_borrow_the_checkouts_commit(
    root, monkeypatch, capsys
) -> None:
    # local/wrappers sits inside the CRAB checkout, which is itself a clone.
    _wrapper(root, "app/w.py", folder="local/wrappers")
    (root / ".gitignore").write_text("local/\n")
    _repo(root, origin="https://github.com/O/R.git")
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["folder"] == str(root / "local" / "wrappers")
    assert data["source"] == {"commit": None, "url": None}


def test_show_a_glob_name_does_not_match_a_tracked_file(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/ab.py")
    _repo(root / "wrappers", origin="https://github.com/O/R.git")
    _wrapper(root, "app/a*.py")
    assert _show(monkeypatch, capsys, "app/a*.py")["source"] == {"commit": None, "url": None}


def test_show_no_github_url_without_an_origin(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    sha = _repo(root / "wrappers")
    assert _show(monkeypatch, capsys, "app/w.py")["source"] == {"commit": sha, "url": None}


@pytest.mark.parametrize(
    "rel", ["app/README.md", "app/_common.py", "app/samples/x/w.py", "app/__init__.py"]
)
def test_show_only_describes_what_list_lists(root, monkeypatch, capsys, rel) -> None:
    _wrapper(root, "app/w.py")
    for name in ("README.md", "_common.py", "samples/x/w.py", "__init__.py"):
        path = root / "wrappers" / "app" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raise SystemExit('imported')\n" if name.endswith(".py") else "# App\n")
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "show", rel, "--json")
    assert code == 2
    assert out == ""
    assert err.startswith(f"crab: wrapper not found: {rel}")


def test_show_nested_search_folders_report_the_folder_the_lookup_hit(
    root, monkeypatch, capsys
) -> None:
    outer = root / "n" / "a"
    _wrapper(root, "x.py", folder="n/a/b")
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{outer}{os.pathsep}{outer / 'b'}")
    data = _show(monkeypatch, capsys, "x.py")
    assert data["relpath"] == "x.py"
    assert data["folder"] == str(outer / "b")


def test_show_reads_a_readme_that_is_not_utf8(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    (root / "wrappers" / "app" / "README.md").write_bytes(b"caf\xe9\n\nText\n")
    data = _show(monkeypatch, capsys, "app/w.py")
    assert data["description"]["source"] == "readme"
    assert data["description"]["text"].startswith("caf")


def test_show_honours_a_source_coding_line(root, monkeypatch, capsys) -> None:
    path = root / "wrappers" / "app" / "w.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# -*- coding: latin-1 -*-\n" + b'"""Caf\xe9 wrapper."""\n' + BODY.encode())
    assert _show(monkeypatch, capsys, "app/w.py")["description"]["text"] == "Caf\xe9 wrapper."


def test_show_readme_links_become_their_text(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    (root / "wrappers" / "app" / "README.md").write_text("[Blink](https://x) is a suite.\n")
    assert _show(monkeypatch, capsys, "app/w.py")["description"]["text"] == "Blink is a suite."


def test_show_human_output(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py", doc=DOC)
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "show", "app/w.py")
    assert code == 0
    assert "app/w.py" in out
    assert "Measures t. Checked with app 1.2." in out
    assert "t (s, converges)" in out
    assert "ok (-, check)" in out
    assert f"binary path: {root / 'bin' / 'onpath'}" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
