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


# A relative path in a receipt means a different file depending on the folder the job starts in
# (the Slurm submit folder, the job folder): it is never looked up, whatever the current folder holds.


@pytest.fixture
def cwd_with_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A current folder (tmp_path/work) that really holds bin/xhpl, also HOME, with a sibling
    tmp_path/x/bin/xhpl for "../x/bin/xhpl"."""
    work = tmp_path / "work"
    (work / "bin").mkdir(parents=True)
    (tmp_path / "x" / "bin").mkdir(parents=True)
    _exe(work / "bin", "xhpl")
    _exe(tmp_path / "x" / "bin", "xhpl")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(work))
    monkeypatch.setenv("PATH", "/nonexistent")
    return work


@pytest.mark.parametrize("relative", ["bin/xhpl", "./bin/xhpl", "../x/bin/xhpl"])
def test_a_relative_receipt_path_is_absent_even_when_the_file_exists_here(
    cwd_with_binary: Path, relative: str
) -> None:
    assert (cwd_with_binary / relative).is_file()
    app = _wrapper(relative)
    assert app.find_binary() == (None, "missing")
    assert app._absent_binary == relative
    with pytest.raises(MissingBinaryError) as exc:
        app.resolve_binary()
    message = str(exc.value)
    assert repr(relative) in message and "relative or unexpanded path" in message
    assert "crab setup" in message and "crab receipts set" in message
    assert "does not exist" not in message


def test_a_tilde_receipt_path_is_absent_and_never_expanded(cwd_with_binary: Path) -> None:
    assert (Path(os.path.expanduser("~")) / "bin" / "xhpl").is_file()
    app = _wrapper("~/bin/xhpl")
    assert app.find_binary() == (None, "missing")
    assert app._absent_binary == "~/bin/xhpl"
    with pytest.raises(MissingBinaryError) as exc:
        app.resolve_binary()
    message = str(exc.value)
    assert "relative or unexpanded path" in message
    assert "crab setup" in message and "crab receipts set" in message


def test_a_launcher_line_with_a_relative_token_reports_that_token(cwd_with_binary: Path) -> None:
    (cwd_with_binary / "opt").mkdir()
    prefix = _exe(cwd_with_binary / "opt", "select_nic")
    app = _wrapper(f"{prefix} bin/xhpl")
    assert app.find_binary() == (None, "missing")
    assert app._absent_binary == "bin/xhpl"


def test_a_relative_receipt_path_falls_through_to_path(
    cwd_with_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (cwd_with_binary / "onpath").mkdir()
    exe = _exe(cwd_with_binary / "onpath", "mybench")
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}/nonexistent")
    app = _wrapper("bin/xhpl", executable="mybench")
    assert app.find_binary() == (str(exe), "path")


def test_an_absolute_existing_path_is_still_the_receipt_binary(cwd_with_binary: Path) -> None:
    exe = cwd_with_binary / "bin" / "xhpl"
    assert _wrapper(str(exe)).find_binary() == (str(exe), "receipt")


def test_bare_command_names_are_still_kept(cwd_with_binary: Path) -> None:
    assert _wrapper("xhpl").find_binary() == ("xhpl", "receipt")
    assert _wrapper("pw.x").find_binary() == ("pw.x", "receipt")


@pytest.mark.parametrize("leftover", ["$HOME/bin/xhpl", "{dir}/bin/xhpl"])
def test_an_unexpanded_leftover_token_is_reported_with_the_same_hint(
    cwd_with_binary: Path, leftover: str
) -> None:
    app = _wrapper(leftover)
    assert app.find_binary() == (None, "missing")
    with pytest.raises(MissingBinaryError) as exc:
        app.resolve_binary()
    assert "relative or unexpanded path" in str(exc.value)
