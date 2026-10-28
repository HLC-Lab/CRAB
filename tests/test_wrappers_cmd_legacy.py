"""`crab parse` on a wrapper that returns the list-per-metric shape (removed in CRAB 1.0)
exits 2 with the reason, instead of converting it. It also finds its WRAPPER: the search path
first, then the file as typed (relative to where it runs)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import crab.core.wrapper_paths as wp

LEGACY_WRAPPER = """from crab.wrappers.base import base


class app(base):
    metadata = [{"name": "lat", "unit": "us", "conv": True}]

    def read_data(self):
        return [[float(x) for x in self.stdout.split()]]
"""


def test_parse_of_a_legacy_wrapper_exits_2_with_the_reason(tmp_path: Path) -> None:
    (tmp_path / "oldapp.py").write_text(LEGACY_WRAPPER)
    (tmp_path / "stdout.txt").write_text("1.5 2.5\n")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from crab.cli.main import cli_router; cli_router()",
            "parse",
            str(tmp_path / "oldapp.py"),
            str(tmp_path / "stdout.txt"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 2
    assert "wrapper 'oldapp'" in out.stderr
    assert "removed in CRAB 1.0" in out.stderr
    assert '[{"lat": 1.0}]' in out.stderr
    assert "docs/extending/wrappers.md" in out.stderr
    assert out.stdout == ""


def _wrapper_returning(value: float) -> str:
    return f"""from crab.wrappers.base import base


class app(base):
    metadata = [{{"name": "lat", "unit": "us", "conv": True}}]

    def read_data(self):
        return [{{"lat": {value}}}]
"""


@pytest.fixture
def search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A one-folder search path, and a launch folder apart from it."""
    folder = tmp_path / "ws"
    folder.mkdir()
    launch = tmp_path / "launch"
    launch.mkdir()
    (tmp_path / "stdout.txt").write_text("x\n")
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path / "crab-root"))
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(folder))
    monkeypatch.chdir(launch)
    return tmp_path


def _parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wrapper: str,
) -> tuple[int, str, str]:
    from crab.cli.main import cli_router

    monkeypatch.setattr(
        sys, "argv", ["crab", "parse", wrapper, str(tmp_path / "stdout.txt"), "--json"]
    )
    try:
        cli_router()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _value(out: str) -> float:
    return json.loads(out)["rows"][0]["lat"]


def test_parse_looks_on_the_search_path_before_the_typed_file(
    search: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (search / "ws" / "suite").mkdir()
    (search / "ws" / "suite" / "w.py").write_text(_wrapper_returning(1.0))
    (search / "launch" / "suite").mkdir()
    (search / "launch" / "suite" / "w.py").write_text(_wrapper_returning(2.0))
    code, out, _err = _parse(search, monkeypatch, capsys, "suite/w.py")
    assert code == 0
    assert _value(out) == 1.0


@pytest.mark.parametrize("typed", ["./w.py", "sub/w.py"])
def test_parse_takes_a_typed_file_relative_to_the_current_folder(
    search: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: str,
) -> None:
    target = search / "launch" / typed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_wrapper_returning(3.0))
    code, out, _err = _parse(search, monkeypatch, capsys, typed)
    assert code == 0
    assert _value(out) == 3.0


def test_parse_of_a_name_found_nowhere_names_what_was_typed_and_the_folders(
    search: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _parse(search, monkeypatch, capsys, "suite/nope.py")
    assert code != 0
    assert out == ""
    assert "wrapper not found: suite/nope.py" in err
    assert str(search / "ws") in err


def test_parse_takes_an_absolute_path(
    search: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = search / "elsewhere.py"
    target.write_text(_wrapper_returning(4.0))
    code, out, _err = _parse(search, monkeypatch, capsys, str(target))
    assert code == 0
    assert _value(out) == 4.0
