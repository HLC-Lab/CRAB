"""A wrapper-supplied or receipt binary path counts only when the file exists; otherwise
`find_binary` falls through to the `executable` on PATH, then to "missing". A bare command name
cannot be checked as a file and is kept. The config `binary` key is the user's explicit choice
and wins even when absent."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from crab.wrappers.base import MissingBinaryError, base


def _wrapper(default: str | None, executable: str = ""):
    class _App(base):
        def get_binary_path(self):
            return default

    _App.executable = executable
    return _App(0, True, "")


def _exe(directory: Path, name: str) -> Path:
    directory.mkdir(exist_ok=True)
    exe = directory / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_an_existing_default_path_is_the_receipt_binary(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "opt", "bench")
    assert _wrapper(str(exe)).find_binary() == (str(exe), "receipt")


def test_an_absent_default_path_without_executable_is_missing(tmp_path: Path) -> None:
    assert _wrapper(str(tmp_path / "nope" / "bench")).find_binary() == (None, "missing")


def test_an_absent_default_path_falls_through_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _exe(tmp_path / "bin", "mybench")
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}/nonexistent")
    app = _wrapper(str(tmp_path / "nope" / "bench"), executable="mybench")
    assert app.find_binary() == (str(exe), "path")


def test_resolve_binary_names_the_absent_path(tmp_path: Path) -> None:
    absent = str(tmp_path / "nope" / "bench")
    with pytest.raises(MissingBinaryError) as exc:
        _wrapper(absent).resolve_binary()
    assert absent in str(exc.value) and "does not exist" in str(exc.value)


def test_a_bare_command_name_is_kept() -> None:
    assert _wrapper("mybench").find_binary() == ("mybench", "receipt")


def test_the_config_binary_wins_even_when_absent(tmp_path: Path) -> None:
    app = _wrapper(str(tmp_path / "default"))
    app.binary = str(tmp_path / "missing" / "chosen")  # what the runner injects
    assert app.find_binary() == (str(tmp_path / "missing" / "chosen"), "config")


def test_a_prefix_command_with_every_path_present_is_kept(tmp_path: Path) -> None:
    prefix = _exe(tmp_path, "prefix")
    binary = _exe(tmp_path / "bin", "x")
    command = f"{prefix} {binary}"
    assert _wrapper(command).find_binary() == (command, "receipt")


def test_a_prefix_command_with_an_absent_binary_falls_through(tmp_path: Path) -> None:
    prefix = _exe(tmp_path, "prefix")
    absent = str(tmp_path / "bin" / "x")
    app = _wrapper(f"{prefix} {absent}")
    assert app.find_binary() == (None, "missing")
    with pytest.raises(MissingBinaryError) as exc:
        app.resolve_binary()
    assert repr(absent) in str(exc.value) and "does not exist" in str(exc.value)
