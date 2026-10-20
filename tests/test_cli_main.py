"""Argparse wiring for `crab.cli.main.cli_router` (the real CLI entry point).

`cli_router` builds its own `ArgumentParser` and calls `args.func(args)`
directly, so the only way to catch a flag missing from a subparser (the
exact failure mode reported as ``unrecognized arguments``) is to invoke it
end to end with a real argv, stubbing out the handler so nothing on disk is
touched. Fake-transport contract tests (`test_web_contract.py`) call the
gatherer functions directly and would pass even if a flag were dropped from
the parser here.
"""

from __future__ import annotations

import sys

import pytest

from crab.cli import main


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str], handler_name: str) -> object:
    captured: dict[str, object] = {}

    def fake_handler(args):
        captured["args"] = args

    monkeypatch.setattr(main, handler_name, fake_handler)
    monkeypatch.setattr(sys, "argv", ["crab", *argv])
    main.cli_router()
    return captured["args"]


def test_logs_experiment_flag_parses(monkeypatch: pytest.MonkeyPatch):
    args = _run_cli(
        monkeypatch,
        ["logs", "--data-dir", "/tmp/job", "--experiment", "01_baseline", "--json"],
        "handle_logs",
    )
    assert args.data_dir == "/tmp/job"
    assert args.experiment == "01_baseline"
    assert args.json is True


def test_run_only_flag_parses(monkeypatch: pytest.MonkeyPatch):
    args = _run_cli(
        monkeypatch,
        ["run", "config.json", "-p", "preset1", "--only", "01_a,02_b", "--json"],
        "handle_run",
    )
    assert args.app_config_file == "config.json"
    assert args.only == "01_a,02_b"
