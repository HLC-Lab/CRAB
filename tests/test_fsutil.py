"""`leave_dir_before_remove`: the process steps out of a folder before CRAB removes it.

Every test first `monkeypatch.chdir(tmp_path)`, so teardown restores the real cwd, and only
then moves into the folder under test.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from crab.fsutil import (
    absolute_binary,
    expand_binary,
    is_path_like,
    leave_dir_before_remove,
    relative_path_word,
)


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


# --- absolute_binary: a typed binary path is stored absolute ---


def _delete_cwd(tmp_path: Path) -> None:
    """Move into a folder and remove it, so `os.getcwd()` raises (the test chdir'd first)."""
    gone = tmp_path / "gone"
    gone.mkdir()
    os.chdir(gone)
    gone.rmdir()


@pytest.mark.parametrize("name", ["xhpl", "pw.x", "mpirun"])
def test_a_bare_command_name_is_unchanged(monkeypatch, tmp_path, name):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary(name) == name


def test_an_absolute_path_is_unchanged_even_without_a_current_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _delete_cwd(tmp_path)
    assert absolute_binary("/opt/x/bin/xhpl") == "/opt/x/bin/xhpl"


def test_a_tilde_path_is_expanded_even_without_a_current_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _delete_cwd(tmp_path)
    assert absolute_binary("~/bin/x") == f"{tmp_path / 'home'}/bin/x"


def test_a_relative_path_is_made_absolute_from_the_current_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    os.chdir(tmp_path / "sub")
    here = os.path.abspath(".")
    assert absolute_binary("rel/x") == f"{here}/rel/x"
    assert absolute_binary("./x") == f"{here}/x"
    assert absolute_binary("../x/y") == f"{tmp_path}/x/y"
    assert absolute_binary("rel/dir/") == f"{here}/rel/dir"


@pytest.mark.parametrize("line", ["/opt/select_nic /opt/bin/x", "  /opt/a   /opt/b  "])
def test_an_absolute_launcher_line_is_unchanged(monkeypatch, tmp_path, line):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary(line) == line


@pytest.mark.parametrize("raw", ["", "   "])
def test_an_empty_value_is_unchanged(monkeypatch, tmp_path, raw):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary(raw) == raw


def test_an_unknown_user_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"~.*full path"):
        absolute_binary("~nosuchuser_zz/x")


@pytest.mark.parametrize("raw", ["$HOME/x", "${SCRATCH}/x"])
def test_an_environment_variable_is_refused(monkeypatch, tmp_path, raw):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"environment variables.*full path"):
        absolute_binary(raw)


def test_a_relative_path_without_a_current_folder_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _delete_cwd(tmp_path)
    with pytest.raises(ValueError, match=r"current folder no longer exists.*full path"):
        absolute_binary("rel/x")


# --- absolute_binary: dots, bare_is_path, launcher lines, normpath ---


def test_dot_and_dotdot_are_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    os.chdir(tmp_path / "sub")
    assert absolute_binary(".") == str(tmp_path / "sub")
    assert absolute_binary("..") == str(tmp_path)


def test_a_bare_name_is_a_path_when_asked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary("build", bare_is_path=True) == f"{tmp_path}/build"
    assert absolute_binary("xhpl", bare_is_path=True) == f"{tmp_path}/xhpl"


def test_a_single_flag_like_token_is_untouched_even_as_a_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary("-n", bare_is_path=True) == "-n"
    assert absolute_binary("-x/y") == "-x/y"


def test_every_path_like_token_of_a_launcher_line_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    here = os.path.abspath(".")
    assert absolute_binary("~/sel ./bin/x") == f"{tmp_path / 'home'}/sel {here}/bin/x"
    assert absolute_binary("mpirun ./x") == f"mpirun {here}/x"
    assert absolute_binary("mpirun -n 4 ./x") == f"mpirun -n 4 {here}/x"


@pytest.mark.parametrize("line", ["/opt/select_nic /opt/bin/x", "mpirun -n 4"])
def test_a_launcher_line_with_nothing_to_change_is_returned_as_given(monkeypatch, tmp_path, line):
    monkeypatch.chdir(tmp_path)
    assert absolute_binary(line) == line


def test_a_launcher_line_with_an_environment_variable_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"environment variables.*full path"):
        absolute_binary("sel $HOME/x")
    with pytest.raises(ValueError, match=r"~.*full path"):
        absolute_binary("~nosuchuser_zz/sel /opt/x")


def test_absolute_results_have_no_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert absolute_binary("~/x/") == f"{tmp_path / 'home'}/x"
    assert absolute_binary("/opt/x/") == "/opt/x"
    assert absolute_binary("x/") == f"{tmp_path}/x"


def test_expand_binary_expands_tilde_and_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XHPL", "/opt/hpl")
    monkeypatch.delenv("NOPE_ZZ", raising=False)
    assert expand_binary("~/bin/x") == f"{tmp_path}/bin/x"
    assert expand_binary("$XHPL/bin/x") == "/opt/hpl/bin/x"
    assert expand_binary("${XHPL}/bin/x") == "/opt/hpl/bin/x"
    assert expand_binary("$NOPE_ZZ/x") == "$NOPE_ZZ/x"
    assert expand_binary("{dir}/x") == "{dir}/x"
    assert expand_binary("mpirun ~/x -n 4") == f"mpirun {tmp_path}/x -n 4"


@pytest.mark.parametrize("value", ["xhpl", "/opt/x/xhpl", "mpirun  /opt/x   -n 4", "", "'a b'"])
def test_expand_binary_returns_a_value_with_nothing_to_expand_exactly(value):
    assert expand_binary(value) == value


@pytest.mark.parametrize(
    ("value", "word"),
    [
        ("bin/x", "bin/x"),
        ("/a/b", None),
        ("xhpl", None),
        ("mpirun ./x", "./x"),
        ("$HOME/x", None),
        ("${SCRATCH}/x", None),
        ("{dir}/x", None),
        ("bin/{n}/x", None),
        ("~/x", None),
        ("/opt/x -n 4", None),
        (".", "."),
        ("..", ".."),
        ("", None),
    ],
)
def test_relative_path_word(value, word):
    assert relative_path_word(value) == word


def test_is_path_like():
    assert is_path_like("a/b") and is_path_like("~") and is_path_like(".")
    assert not is_path_like("xhpl") and not is_path_like("-x/y")
