"""Wrappers are looked up along a search path: the untracked `local/wrappers` first (private or
in-progress wrappers), then each folder in CRAB_PATH_WRAPPERS (a `:`-separated list), or the
checkout's `wrappers/` when it is unset. A path found nowhere resolves as it did before the
search path existed, so old configs behave the same."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import crab.core.wrapper_paths as wp
from crab.cli import contract


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    for d in ("local/wrappers/blink", "wrappers/blink", "extra/blink"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def _touch(p: Path) -> Path:
    p.write_text("class app: pass\n")
    return p


def test_the_checkout_wrappers_folder_is_the_default(root: Path) -> None:
    shared = _touch(root / "wrappers/blink/a2a_b.py")
    assert wp.resolve_wrapper_path("blink/a2a_b.py") == str(shared)


def test_a_local_wrapper_shadows_the_shared_one(root: Path) -> None:
    _touch(root / "wrappers/blink/a2a_b.py")
    mine = _touch(root / "local/wrappers/blink/a2a_b.py")
    assert wp.resolve_wrapper_path("blink/a2a_b.py") == str(mine)


def test_crab_path_wrappers_may_list_several_folders(root: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CRAB_PATH_WRAPPERS", os.pathsep.join([str(root / "wrappers"), str(root / "extra")])
    )
    found = _touch(root / "extra/blink/x.py")
    assert wp.resolve_wrapper_path("blink/x.py") == str(found)
    assert wp.wrapper_search_path() == [
        str(root / "local/wrappers"),
        str(root / "wrappers"),
        str(root / "extra"),
    ]


def test_not_found_keeps_the_old_answer(root: Path, monkeypatch) -> None:
    assert wp.resolve_wrapper_path("blink/nope.py") == "blink/nope.py"  # cwd-relative, as before
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(root / "wrappers"))
    assert wp.resolve_wrapper_path("blink/nope.py") == str(root / "wrappers/blink/nope.py")


def test_absolute_paths_are_untouched(root: Path) -> None:
    assert wp.resolve_wrapper_path("/opt/w.py") == "/opt/w.py"


def test_list_benchmarks_walks_the_search_path_first_match_wins(root: Path) -> None:
    _touch(root / "wrappers/blink/a2a_b.py")
    _touch(root / "wrappers/blink/ring_nb.py")
    _touch(root / "local/wrappers/blink/a2a_b.py")
    _touch(root / "local/wrappers/blink/_helper.py")
    wrappers = contract.gather_benchmarks(env_dir=root / "none")["wrappers"]
    by_rel = {w["relpath"]: w["path"] for w in wrappers}
    assert by_rel == {
        "blink/a2a_b.py": str(root / "local/wrappers/blink/a2a_b.py"),
        "blink/ring_nb.py": str(root / "wrappers/blink/ring_nb.py"),
    }


def test_a_path_python_cannot_import_fails_naming_the_file(tmp_path: Path) -> None:
    """`importlib.util.spec_from_file_location` returns None for a file without a Python suffix;
    `load_module` used to crash on it with "'NoneType' object has no attribute 'loader'"."""
    not_python = tmp_path / "a2a_b.txt"
    not_python.write_text("class app: pass\n")
    with pytest.raises(ImportError, match=r"a2a_b\.txt"):
        wp.load_module(str(not_python))
