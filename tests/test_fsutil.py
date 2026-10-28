"""`leave_dir_before_remove`: the process steps out of a folder before CRAB removes it.

Every test first `monkeypatch.chdir(tmp_path)`, so teardown restores the real cwd, and only
then moves into the folder under test.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from crab.fsutil import leave_dir_before_remove


def _real(path: Path) -> str:
    return os.path.realpath(path)


def test_cwd_in_a_nested_subfolder_moves_to_the_parent_of_target(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "benchmarks" / "blink"
    nested = target / "build" / "deep"
    nested.mkdir(parents=True)
    os.chdir(nested)

    leave_dir_before_remove(target)

    assert os.getcwd() == _real(target.parent)
    assert os.environ["PWD"] == _real(target.parent)
    shutil.rmtree(target)
    assert os.getcwd() == _real(target.parent)


def test_cwd_equal_to_target_moves_to_the_parent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "benchmarks" / "blink"
    target.mkdir(parents=True)
    os.chdir(target)

    leave_dir_before_remove(target)

    assert os.getcwd() == _real(target.parent)
    shutil.rmtree(target)
    assert os.getcwd() == _real(target.parent)


def test_cwd_outside_target_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    sibling = tmp_path / "target-sibling"
    target.mkdir()
    sibling.mkdir()
    os.chdir(sibling)
    before = os.getcwd()

    leave_dir_before_remove(target)

    assert os.getcwd() == before


def test_a_deleted_cwd_is_left_whatever_the_target(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    gone = tmp_path / "gone"
    gone.mkdir()
    os.chdir(gone)
    gone.rmdir()
    target = tmp_path / "other" / "target"
    target.mkdir(parents=True)

    leave_dir_before_remove(target)

    assert os.getcwd() == _real(target.parent)


def test_a_missing_target_with_a_working_cwd_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    before = os.getcwd()

    leave_dir_before_remove(tmp_path / "does-not-exist")

    assert os.getcwd() == before


def test_a_deleted_cwd_and_a_missing_parent_fall_back_to_home(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    gone = tmp_path / "gone"
    gone.mkdir()
    os.chdir(gone)
    gone.rmdir()

    leave_dir_before_remove(tmp_path / "missing-parent" / "target")

    assert os.getcwd() == _real(home)


def test_cwd_reached_through_a_symlink_into_target_is_moved_out(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "real" / "target"
    (target / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(target / "sub")
    os.chdir(link)

    leave_dir_before_remove(target)

    assert os.getcwd() == _real(target.parent)


def test_benchmarks_symlink_resolves_to_the_real_benchmarks_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    real_benchmarks = tmp_path / "elsewhere" / "benchmarks"
    (real_benchmarks / "blink").mkdir(parents=True)
    link = tmp_path / "benchmarks"
    link.symlink_to(real_benchmarks)
    os.chdir(real_benchmarks / "blink")

    leave_dir_before_remove(link / "blink")

    assert os.getcwd() == _real(real_benchmarks)
