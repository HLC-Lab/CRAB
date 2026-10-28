"""
setup/recipes/blink.py: current upstream Blink builds with CMake into <clone>/build/bin.

Upstream dropped its Makefile (commit 4ca3338c): the top-level CMakeLists.txt puts every
executable in <build>/bin and names each after its source file (pingpong_b). BLINK_TESTS
defaults ON and fetches googletest from GitHub, hence -DBLINK_TESTS=OFF.
"""

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from crab.setup.recipes.blink import BlinkRecipe

REPO_URL = "https://github.com/HLC-Lab/blink.git"


def _make_exe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _build(recipe, target, env=None, fail_at=None):
    """Run download_and_build with run_command_streamed patched; fail_at is a call index."""
    calls = []

    def fake_run(cmd, cwd, step_name, env, log_callback):
        calls.append((cmd, cwd, step_name, env))
        return fail_at != len(calls) - 1

    with patch.object(recipe, "run_command_streamed", side_effect=fake_run):
        result = recipe.download_and_build(str(target), {}, env if env is not None else {}, None)
    return result, calls


def test_calls_are_clone_configure_build_without_make(tmp_path):
    target = tmp_path / "blink"
    _make_exe(target / "build" / "bin" / "pingpong_b")

    _, calls = _build(BlinkRecipe(), target)

    assert len(calls) == 3
    assert calls[0][0] == ["git", "clone", REPO_URL, str(target)]

    configure = calls[1][0]
    assert configure[0] == "cmake"
    assert "-S" in configure and configure[configure.index("-S") + 1] == str(target)
    assert "-B" in configure and configure[configure.index("-B") + 1] == str(target / "build")
    assert "-DCMAKE_BUILD_TYPE=Release" in configure
    assert "-DBLINK_TESTS=OFF" in configure
    assert "-DBLINK_MPI_WRAPPER=ON" not in configure

    build = calls[2][0]
    assert build[:3] == ["cmake", "--build", str(target / "build")]
    assert "-j" in build or "--parallel" in build

    assert all(call[0][0] != "make" for call in calls)


def test_configure_env_sets_mpi_compilers_and_keeps_caller_env(tmp_path):
    target = tmp_path / "blink"
    caller_env = {"PATH": "/some/bin", "HOME": "/home/x"}
    snapshot = dict(caller_env)

    _, calls = _build(BlinkRecipe(), target, env=caller_env)

    configure_env = calls[1][3]
    assert configure_env["CC"] == "mpicc"
    assert configure_env["CXX"] == "mpicxx"
    assert configure_env["PATH"] == "/some/bin"
    assert configure_env["HOME"] == "/home/x"
    assert caller_env == snapshot


def test_success_returns_build_bin_when_pingpong_exists(tmp_path):
    target = tmp_path / "blink"
    _make_exe(target / "build" / "bin" / "pingpong_b")

    (ok, result, _msg), _ = _build(BlinkRecipe(), target)

    assert ok is True
    assert result is not None
    assert result.binary_path == str(target / "build" / "bin")


def test_failure_when_pingpong_missing_mentions_build_bin(tmp_path):
    target = tmp_path / "blink"
    (target / "build" / "bin").mkdir(parents=True)

    (ok, result, msg), _ = _build(BlinkRecipe(), target)

    assert ok is False
    assert result is None
    assert "build/bin" in msg


def test_failed_step_stops_and_names_the_step(tmp_path):
    target = tmp_path / "blink"
    _make_exe(target / "build" / "bin" / "pingpong_b")
    expected = {0: "clone", 1: "configure", 2: "build"}

    for index, word in expected.items():
        (ok, result, msg), calls = _build(BlinkRecipe(), target, fail_at=index)
        assert ok is False
        assert result is None
        assert word in msg.lower()
        assert len(calls) == index + 1


def test_verify_existing_needs_executable_pingpong_b(tmp_path):
    recipe = BlinkRecipe()

    good = tmp_path / "good"
    _make_exe(good / "pingpong_b")
    assert recipe.verify_existing(str(good)) is True

    old = tmp_path / "old"
    _make_exe(old / "ping-pong_b")
    assert recipe.verify_existing(str(old)) is False

    not_exec = tmp_path / "plain"
    (not_exec).mkdir()
    (not_exec / "pingpong_b").write_text("x")
    (not_exec / "pingpong_b").chmod(0o644)
    assert recipe.verify_existing(str(not_exec)) is False

    assert recipe.verify_existing(str(tmp_path / "missing")) is False


def test_fast_search_finds_cmake_layout_only(tmp_path):
    recipe = BlinkRecipe()

    new_dir = tmp_path / "new"
    _make_exe(new_dir / "blink" / "build" / "bin" / "pingpong_b")
    assert recipe.fast_search(str(new_dir)) == str(new_dir / "blink" / "build" / "bin")

    old_dir = tmp_path / "old"
    _make_exe(old_dir / "blink" / "bin" / "ping-pong_b")
    assert recipe.fast_search(str(old_dir)) is None


def _path_with(tmp_path: Path, names: list[str]) -> str:
    bin_dir = tmp_path / "fakebin"
    for name in names:
        _make_exe(bin_dir / name)
    return str(bin_dir)


def test_check_dependencies_requires_cmake(tmp_path):
    path = _path_with(tmp_path, ["make", "mpicc"])

    ok, msg = BlinkRecipe().check_dependencies({"PATH": path})

    assert ok is False
    assert "CMake" in msg


def test_check_dependencies_requires_mpicc(tmp_path):
    path = _path_with(tmp_path, ["cmake"])

    ok, msg = BlinkRecipe().check_dependencies({"PATH": path})

    assert ok is False
    assert "mpicc" in msg


def test_check_dependencies_passes_without_make(tmp_path):
    path = _path_with(tmp_path, ["cmake", "mpicc"])

    ok, _msg = BlinkRecipe().check_dependencies({"PATH": path})

    assert ok is True
    assert not os.path.exists(os.path.join(path, "make"))


def test_clone_runs_from_the_folder_above_the_target(tmp_path):
    parent = tmp_path / "benchmarks"
    parent.mkdir()

    _, calls = _build(BlinkRecipe(), parent / "blink", fail_at=0)

    assert calls[0][1] == str(parent)
    assert os.path.isabs(calls[0][1])


def test_clone_folder_is_created_when_missing(tmp_path):
    parent = tmp_path / "does" / "not" / "exist"

    _, calls = _build(BlinkRecipe(), parent / "blink", fail_at=0)

    assert parent.is_dir()
    assert calls[0][1] == str(parent)


@pytest.mark.parametrize("cwd", [".", "rel/dir", ""])
def test_run_command_streamed_refuses_a_relative_folder(cwd):
    with pytest.raises(ValueError, match="absolute"):
        BlinkRecipe().run_command_streamed(["true"], cwd, "x", {}, None)


def test_run_command_streamed_runs_in_an_absolute_folder(tmp_path):
    ok = BlinkRecipe().run_command_streamed(
        [sys.executable, "-c", "pass"], str(tmp_path), "x", {}, None
    )

    assert ok is True


def test_run_command_streamed_missing_absolute_folder_returns_false(tmp_path):
    ok = BlinkRecipe().run_command_streamed(
        [sys.executable, "-c", "pass"], str(tmp_path / "missing"), "x", {}, None
    )

    assert ok is False


def test_run_command_streamed_ignores_a_deleted_process_folder(tmp_path, monkeypatch):
    # The bug: crab setup removed the folder it was started in, then cloned from ".".
    monkeypatch.chdir(tmp_path)
    gone = tmp_path / "gone"
    gone.mkdir()
    os.chdir(gone)
    gone.rmdir()

    ok = BlinkRecipe().run_command_streamed(
        [sys.executable, "-c", "pass"], str(tmp_path), "x", {}, None
    )

    assert ok is True
