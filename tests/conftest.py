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

The autouse ``_no_user_presets`` fixture keeps a developer's real preset folders out of every test:
the shared preset folders resolve to none (``crab.cli.presets.shared_preset_folders`` returns ``[]``)
and the override dir to a path that does not exist (``paths.user_presets_dir``); the user config dir
sits under ``tmp_path``. Until the package presets file is removed every test therefore
resolves presets from the package file alone. ``CRAB_PATH_PRESETS`` and ``CRAB_PATH_WRAPPERS`` are not
set for this because the mpirun launcher exports every ``CRAB_*`` variable of the environment with
``-x``, which would change the launch lines the launcher tests expect. The ``real_preset_dirs``
fixture puts the two real functions back for the tests that exercise them; such a test then writes
its folders and sets the variables itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from crab import paths
from crab.cli import presets
from signal_guard import REAL_KILL, REAL_KILLPG, guarded

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

REAL_SHARED_PRESET_FOLDERS = presets.shared_preset_folders
REAL_USER_PRESETS_DIR = paths.user_presets_dir
REPO = Path(__file__).resolve().parent.parent
FIXTURE_WRAPPERS = Path(__file__).resolve().parent / "fixtures" / "wrappers"

# Tests of the shipped wrappers themselves need the crab-wrappers checkout in wrappers/.
# Tests of CRAB's own behaviour use FIXTURE_WRAPPERS instead and never carry this marker.
requires_wrappers_checkout = pytest.mark.skipif(
    not (REPO / "wrappers").is_dir(),
    reason="needs the crab-wrappers checkout in wrappers/ (crab update clones it)",
)


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


@pytest.fixture(autouse=True)
def _no_user_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CRAB_PATH_PRESETS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-user-config"))
    monkeypatch.setattr(presets, "shared_preset_folders", lambda crab_root: [])
    monkeypatch.setattr(paths, "user_presets_dir", lambda root: tmp_path / "no-user-presets")


@pytest.fixture
def real_preset_dirs(monkeypatch: pytest.MonkeyPatch, _no_user_presets: None) -> None:
    """Undo the isolation of the shared presets folders and the override dir for one test."""
    monkeypatch.setattr(presets, "shared_preset_folders", REAL_SHARED_PRESET_FOLDERS)
    monkeypatch.setattr(paths, "user_presets_dir", REAL_USER_PRESETS_DIR)
