"""`crab web` offers to install the web extra when its packages are missing.

`crab.web.run` imports uvicorn lazily inside `run_server`, so importing it succeeds without the
extra. The CLI therefore checks the packages itself before importing. Everything here is faked:
no pip, no server, no prompt.
"""

from __future__ import annotations

import argparse
import importlib.util
from typing import Any

import pytest

from crab import paths
from crab.cli import main as cli_main

INSTALL_ARGS = ["fake-python", "-m", "pip", "install", "-e", "/fake/checkout[web]"]
_real_find_spec = importlib.util.find_spec


class Harness:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.installs: list[list[str]] = []
        self.server_calls: list[dict[str, Any]] = []


def _install(monkeypatch: pytest.MonkeyPatch, *, missing: set[str], answer: str | None) -> Harness:
    """Patch every boundary. `answer` None makes input() raise EOFError."""
    h = Harness()

    def fake_find_spec(name: str, *a: Any, **kw: Any) -> Any:
        if name in {"fastapi", "uvicorn", "asyncssh"}:
            return None if name in missing else object()
        return _real_find_spec(name, *a, **kw)

    def fake_input(prompt: str = "") -> str:
        h.prompts.append(prompt)
        if answer is None:
            raise EOFError
        return answer

    def fake_check_call(cmd: list[str], *a: Any, **kw: Any) -> int:
        h.installs.append(cmd)
        missing.clear()  # the install "worked": the packages are importable now
        return 0

    def fake_run_server(**kw: Any) -> None:
        h.server_calls.append(kw)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli_main.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(cli_main, "_local_install_args", lambda extra, root=None: INSTALL_ARGS)
    monkeypatch.setattr("crab.web.run.run_server", fake_run_server)
    return h


def _args() -> argparse.Namespace:
    return argparse.Namespace(host="127.0.0.1", port=8899, no_browser=True, verbose=True)


def test_missing_uvicorn_declined_aborts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    h = _install(monkeypatch, missing={"uvicorn"}, answer="n")
    cli_main.handle_web(_args())
    out = capsys.readouterr().out
    assert "are not installed" in out
    assert "Aborting." in out
    assert len(h.prompts) == 1
    assert h.server_calls == []
    assert h.installs == []


def test_missing_uvicorn_non_interactive_returns_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _install(monkeypatch, missing={"uvicorn"}, answer=None)
    cli_main.handle_web(_args())
    assert len(h.prompts) == 1
    assert h.server_calls == []
    assert h.installs == []


def test_missing_uvicorn_accepted_installs_then_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _install(monkeypatch, missing={"uvicorn"}, answer="y")
    cli_main.handle_web(_args())
    assert h.installs == [INSTALL_ARGS]
    assert h.server_calls == [
        {"host": "127.0.0.1", "port": 8899, "open_browser": False, "verbose": True}
    ]


def test_all_present_runs_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _install(monkeypatch, missing=set(), answer="n")
    cli_main.handle_web(_args())
    assert h.prompts == []
    assert h.installs == []
    assert h.server_calls == [
        {"host": "127.0.0.1", "port": 8899, "open_browser": False, "verbose": True}
    ]


def _failing_install(monkeypatch: pytest.MonkeyPatch) -> Harness:
    h = _install(monkeypatch, missing={"uvicorn"}, answer="y")

    def failing_check_call(cmd: list[str], *a: Any, **kw: Any) -> int:
        raise cli_main.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(cli_main.subprocess, "check_call", failing_check_call)
    return h


def test_failed_install_names_the_pipx_command_when_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    h = _failing_install(monkeypatch)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    cli_main.handle_web(_args())
    out = capsys.readouterr().out
    assert 'pipx install --force "crab-hpc[web]"' in out
    assert "from your CRAB checkout" not in out
    assert h.server_calls == []


def test_failed_install_names_the_checkout_command_in_a_checkout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    h = _failing_install(monkeypatch)
    monkeypatch.setattr(paths, "is_checkout", lambda: True)
    cli_main.handle_web(_args())
    out = capsys.readouterr().out
    assert "Please run 'pip install -e .[web]' from your CRAB checkout to fix this." in out
    assert "pipx" not in out
    assert h.server_calls == []
