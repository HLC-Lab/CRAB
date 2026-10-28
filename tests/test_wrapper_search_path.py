"""Wrappers are looked up along a search path: the untracked `local/wrappers` first (private or
in-progress wrappers), then each folder in CRAB_PATH_WRAPPERS (a `:`-separated list), or the
checkout's `wrappers/` when it is unset. A path found nowhere resolves as it did before the
search path existed, so old configs behave the same."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import crab.core.wrapper_paths as wp
from crab.cli import contract
from crab.core.experiment.runner import ExperimentRunner


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    for d in ("local/wrappers/blink", "wrappers/blink", "extra/blink"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("class app: pass\n")
    return p


def test_the_checkout_wrappers_folder_is_the_default(root: Path) -> None:
    shared = _touch(root / "wrappers/blink/alltoall_b.py")
    assert wp.resolve_wrapper_path("blink/alltoall_b.py") == str(shared)


def test_a_local_wrapper_shadows_the_shared_one(root: Path) -> None:
    _touch(root / "wrappers/blink/alltoall_b.py")
    mine = _touch(root / "local/wrappers/blink/alltoall_b.py")
    assert wp.resolve_wrapper_path("blink/alltoall_b.py") == str(mine)


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
    _touch(root / "wrappers/blink/alltoall_b.py")
    _touch(root / "wrappers/blink/ring_nb.py")
    _touch(root / "local/wrappers/blink/alltoall_b.py")
    _touch(root / "local/wrappers/blink/_helper.py")
    wrappers = contract.gather_benchmarks(env_dir=root / "none")["wrappers"]
    by_rel = {w["relpath"]: w["path"] for w in wrappers}
    assert by_rel == {
        "blink/alltoall_b.py": str(root / "local/wrappers/blink/alltoall_b.py"),
        "blink/ring_nb.py": str(root / "wrappers/blink/ring_nb.py"),
    }


def test_list_benchmarks_skips_samples_and_caches(root: Path) -> None:
    _touch(root / "wrappers/app/w.py")
    _touch(root / "wrappers/app/samples/case1/helper.py")
    _touch(root / "wrappers/app/__pycache__/w.cpython-312.py")
    _touch(root / "wrappers/app/_common.py")
    wrappers = contract.gather_benchmarks(env_dir=root / "none")["wrappers"]
    assert [w["relpath"] for w in wrappers] == ["app/w.py"]


def test_a_path_python_cannot_import_fails_naming_the_file(tmp_path: Path) -> None:
    """`importlib.util.spec_from_file_location` returns None for a file without a Python suffix;
    `load_module` used to crash on it with "'NoneType' object has no attribute 'loader'"."""
    not_python = tmp_path / "a2a_b.txt"
    not_python.write_text("class app: pass\n")
    with pytest.raises(ImportError, match=r"a2a_b\.txt"):
        wp.load_module(str(not_python))


# Old flat paths (`blink/alltoall_b.py`) after the Blink wrappers moved into one subfolder per
# upstream group (`blink/alltoall/alltoall_b.py`): a single moved file is found, with a note.


def test_an_old_flat_path_finds_the_single_moved_file(root: Path) -> None:
    moved = _touch(root / "wrappers/blink/alltoall/alltoall_b.py")
    assert wp.resolve_wrapper_path("blink/alltoall_b.py") == str(moved)


def test_the_moved_note_names_the_old_and_the_new_path(root: Path) -> None:
    _touch(root / "wrappers/blink/alltoall/alltoall_b.py")
    note = wp.moved_wrapper_note("blink/alltoall_b.py")
    assert note is not None
    assert "blink/alltoall_b.py" in note
    assert "blink/alltoall/alltoall_b.py" in note
    assert "update the config" in note


def test_no_note_for_a_direct_hit_an_absolute_path_or_a_miss(root: Path) -> None:
    _touch(root / "wrappers/blink/ring_nb.py")
    assert wp.moved_wrapper_note("blink/ring_nb.py") is None
    assert wp.moved_wrapper_note("/opt/w.py") is None
    assert wp.moved_wrapper_note("blink/nope.py") is None


def test_two_subfolder_matches_are_ambiguous_so_no_fallback(root: Path, monkeypatch) -> None:
    _touch(root / "wrappers/blink/a/x.py")
    _touch(root / "wrappers/blink/b/x.py")
    assert wp.resolve_wrapper_path("blink/x.py") == "blink/x.py"  # the plain miss answer
    assert wp.moved_wrapper_note("blink/x.py") is None
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(root / "wrappers"))
    assert wp.resolve_wrapper_path("blink/x.py") == str(root / "wrappers/blink/x.py")


def test_a_direct_hit_wins_over_a_subfolder_copy(root: Path) -> None:
    direct = _touch(root / "wrappers/blink/alltoall_b.py")
    _touch(root / "wrappers/blink/alltoall/alltoall_b.py")
    assert wp.resolve_wrapper_path("blink/alltoall_b.py") == str(direct)
    assert wp.moved_wrapper_note("blink/alltoall_b.py") is None


def test_the_fallback_looks_one_level_down_only(root: Path) -> None:
    _touch(root / "wrappers/blink/alltoall/missing.py")
    _touch(root / "wrappers/x.py")  # a bare filename must not match `*/x.py` either
    _touch(root / "wrappers/blink/deep/er/y.py")
    for path in ("blink/alltoall/missing.py", "x.py", "blink/y.py"):
        assert wp.moved_wrapper_note(path) is None
    assert wp.resolve_wrapper_path("blink/y.py") == "blink/y.py"
    # a bare filename that is not in the search path: no fallback into subfolders either
    _touch(root / "wrappers/blink/z.py")
    assert wp.resolve_wrapper_path("z.py") == "z.py"
    assert wp.moved_wrapper_note("z.py") is None


def test_the_fallback_follows_the_search_order(root: Path, monkeypatch) -> None:
    mine = _touch(root / "local/wrappers/blink/alltoall/x.py")
    _touch(root / "wrappers/blink/other/x.py")
    assert wp.resolve_wrapper_path("blink/x.py") == str(mine)
    assert wp.moved_wrapper_note("blink/x.py") == (
        "wrapper blink/x.py moved to blink/alltoall/x.py: update the config"
    )


def test_the_first_folder_with_a_match_decides_even_when_it_is_ambiguous(root: Path) -> None:
    _touch(root / "local/wrappers/blink/a/x.py")
    _touch(root / "local/wrappers/blink/b/x.py")
    _touch(root / "wrappers/blink/c/x.py")
    assert wp.resolve_wrapper_path("blink/x.py") == "blink/x.py"
    assert wp.moved_wrapper_note("blink/x.py") is None


def test_the_runner_warns_once_with_the_new_path_for_an_old_flat_path(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warning comes from `moved_wrapper_note`; loading is stopped right after the lookup."""
    moved = _touch(root / "wrappers/blink/alltoall/alltoall_b.py")
    loaded: list[str] = []

    def stop(path: str) -> None:
        loaded.append(path)
        raise RuntimeError("stop")

    monkeypatch.setattr("crab.core.experiment.runner.load_module", stop)
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": "blink/alltoall_b.py", "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(root / "system" / "job"),
        logger=MagicMock(),
    )
    with pytest.raises(RuntimeError, match="stop"):
        runner.setup()
    assert loaded == [str(moved)]
    warnings = [c.args[0] for c in runner.log.warning.call_args_list]
    assert len(warnings) == 1
    assert "blink/alltoall/alltoall_b.py" in warnings[0]


def test_a_relative_crab_path_wrappers_entry_is_refused(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{root / 'wrappers'}{os.pathsep}rel/b")
    with pytest.raises(ValueError, match="CRAB_PATH_WRAPPERS"):
        wp.wrapper_search_path()
    with pytest.raises(ValueError, match="CRAB_PATH_WRAPPERS"):
        wp.resolve_wrapper_path("blink/nope.py")


def test_a_tilde_entry_becomes_an_absolute_path_under_home(
    root: Path, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "~/w")
    assert wp.wrapper_search_path() == [str(root / "local/wrappers"), str(tmp_path / "home" / "w")]
    assert wp.resolve_wrapper_path("blink/nope.py") == str(
        tmp_path / "home" / "w" / "blink/nope.py"
    )


def test_a_dollar_entry_stays_unexpanded(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "$NO_SUCH_VARIABLE_FOR_CRAB/w")
    assert wp.wrapper_search_path() == [
        str(root / "local/wrappers"),
        "$NO_SUCH_VARIABLE_FOR_CRAB/w",
    ]
