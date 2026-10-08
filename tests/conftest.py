"""Shared web-test helpers.

The localhost API requires a per-session token and a local Host header
(see src/crab/web/server.py api_guard). ``auth_client`` builds a TestClient
that authenticates like the real SPA does, so route tests exercise the routes
rather than the guard (test_web_security.py covers the guard itself).

The autouse ``_signal_guard`` fixture replaces ``os.kill`` and ``os.killpg`` for every test with
a wrapper from ``signal_guard`` that fails the test on a call that could signal every process of
the user or pytest's own group (pid <= 1, a bool, the own group). A test that patches either
function itself overrides the guard for that test. ``signal_guard`` imports as a top-level module
because pytest's default "prepend" import mode puts ``tests/`` (no ``__init__.py``) on sys.path.

The autouse ``_isolated_state_home`` fixture points ``XDG_STATE_HOME`` at a per-test temporary
directory, so the local scheduler's job records (``$XDG_STATE_HOME/crab/jobs/``) never reach the
real ``~/.local/state``. A test that wants its own location sets the variable again.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from signal_guard import REAL_KILL, REAL_KILLPG, guarded

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient


def auth_client(app: FastAPI, **kwargs) -> TestClient:
    from fastapi.testclient import TestClient

    return TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-Crab-Token": app.state.api_token},
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _signal_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "kill", guarded("kill", REAL_KILL))
    monkeypatch.setattr(os, "killpg", guarded("killpg", REAL_KILLPG))


@pytest.fixture(autouse=True)
def _isolated_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
