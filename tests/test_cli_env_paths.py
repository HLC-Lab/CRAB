"""The CLI refuses a relative folder variable at startup: one line on stderr, exit status 2."""

from __future__ import annotations

import sys

import pytest

from crab.cli.main import cli_router


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CRAB_HOME",
        "CRAB_PATH_WRAPPERS",
        "CRAB_PATH_PRESETS",
        "CRAB_WEB_CONFIG_DIR",
        "CRAB_WEB_DATA_DIR",
        "CRAB_WEB_LIBRARY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def _assert_one_line_refusal(captured, name: str) -> None:
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("crab: ")
    assert name in lines[0]
    assert "absolute" in lines[0]
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_relative_crab_home_stops_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRAB_HOME", "rel/home")
    monkeypatch.setattr(sys, "argv", ["crab", "info", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli_router()
    assert exc.value.code == 2
    _assert_one_line_refusal(capsys.readouterr(), "CRAB_HOME")


def test_relative_wrappers_entry_stops_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "/abs:rel/w")
    monkeypatch.setattr(sys, "argv", ["crab", "wrappers", "list", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli_router()
    assert exc.value.code == 2
    _assert_one_line_refusal(capsys.readouterr(), "CRAB_PATH_WRAPPERS")


def test_a_valid_environment_still_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRAB_HOME", "/abs/home")
    monkeypatch.setattr(sys, "argv", ["crab", "info", "--json"])
    cli_router()
    captured = capsys.readouterr()
    assert captured.out.lstrip().startswith("{")
    assert "absolute" not in captured.err


def test_help_works_with_a_relative_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRAB_HOME", "rel/home")
    monkeypatch.setattr(sys, "argv", ["crab", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli_router()
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out
