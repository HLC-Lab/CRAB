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

``WRAPPERS_PRESETS`` is the presets folder of a crab-wrappers checkout: ``wrappers/presets`` when
that clone is on ``main`` (CI), else the sibling ``../crab-wrappers/presets``, else ``None``. Tests of
the real presets use ``wrappers_presets()`` (the folder resolved as one shared layer) and carry
``requires_wrappers_presets``, which skips them without a folder. ``CRAB_PATH_WRAPPERS`` is not used
for this (see below).

The autouse ``_no_user_presets`` fixture keeps a developer's real preset folders out of every test:
the shared preset folders resolve to none (``crab.cli.presets.shared_preset_folders`` returns ``[]``)
and the override dir to a path that does not exist (``paths.user_presets_dir``); the user config dir
sits under ``tmp_path``. The package layer is empty too: ``paths.shipped_presets`` returns a
per-test file holding ``{}`` (outside ``tmp_path``), so no test reads the package's own presets
file. A test that needs fixture presets takes the ``shared_presets`` fixture:
``shared_presets(presets, filename)`` writes them into one temporary shared folder, the only shared
folder of that test. ``CRAB_PATH_PRESETS`` and ``CRAB_PATH_WRAPPERS`` are not set for this because
the mpirun launcher exports every ``CRAB_*`` variable of the environment with ``-x``, which would
change the launch lines the launcher tests expect. The ``real_preset_dirs`` fixture puts the two
real folder functions back for the tests that exercise them; such a test then writes its folders and
sets the variables itself. ``real_shipped_presets`` puts back the real ``paths.shipped_presets``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from crab import paths
from crab.cli import presets
from signal_guard import REAL_KILL, REAL_KILLPG, guarded

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

REAL_SHARED_PRESET_FOLDERS = presets.shared_preset_folders
REAL_USER_PRESETS_DIR = paths.user_presets_dir
REAL_SHIPPED_PRESETS = paths.shipped_presets
REPO = Path(__file__).resolve().parent.parent
FIXTURE_WRAPPERS = Path(__file__).resolve().parent / "fixtures" / "wrappers"

# Tests of the shipped wrappers themselves need the crab-wrappers checkout in wrappers/.
# Tests of CRAB's own behaviour use FIXTURE_WRAPPERS instead and never carry this marker.
requires_wrappers_checkout = pytest.mark.skipif(
    not (REPO / "wrappers").is_dir(),
    reason="needs the crab-wrappers checkout in wrappers/ (crab update clones it)",
)

_PRESET_FOLDER_CANDIDATES = (
    REPO / "wrappers" / "presets",
    REPO.parent / "crab-wrappers" / "presets",
)
WRAPPERS_PRESETS: Path | None = next(
    (folder for folder in _PRESET_FOLDER_CANDIDATES if folder.is_dir()), None
)
requires_wrappers_presets = pytest.mark.skipif(
    WRAPPERS_PRESETS is None,
    reason=(
        "needs crab-wrappers with presets/: wrappers/ on main (crab update --head) "
        "or a sibling ../crab-wrappers checkout"
    ),
)


def wrappers_presets() -> dict[str, dict[str, Any]]:
    """The real presets of the crab-wrappers checkout, resolved as one shared layer.

    Raises:
        RuntimeError: no checkout with presets/ was found (tests carry `requires_wrappers_presets`).
    """
    if WRAPPERS_PRESETS is None:
        raise RuntimeError("no crab-wrappers presets folder found")
    return presets.resolve_presets([presets._read_folder(WRAPPERS_PRESETS)], [])


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
def _no_user_presets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("CRAB_PATH_PRESETS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-user-config"))
    monkeypatch.setattr(presets, "shared_preset_folders", lambda crab_root: [])
    monkeypatch.setattr(paths, "user_presets_dir", lambda root: tmp_path / "no-user-presets")
    # Outside tmp_path: tests that list their tmp_path must not see the file.
    empty_package = tmp_path_factory.mktemp("no-package-presets") / "presets.json"
    empty_package.write_text("{}")
    monkeypatch.setattr(paths, "shipped_presets", lambda: empty_package)


@pytest.fixture
def shared_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., Path]:
    """A writer for fixture presets: `write(presets, filename="fixture.json")` returns the file.

    The files land in one temporary folder that becomes the only shared presets folder.
    """
    folder = tmp_path / "shared-presets"
    monkeypatch.setattr(presets, "shared_preset_folders", lambda crab_root: [folder])

    def write(fixture_presets: dict[str, Any], filename: str = "fixture.json") -> Path:
        folder.mkdir(exist_ok=True)
        file = folder / filename
        file.write_text(json.dumps(fixture_presets))
        return file

    return write


@pytest.fixture
def real_preset_dirs(monkeypatch: pytest.MonkeyPatch, _no_user_presets: None) -> None:
    """Undo the isolation of the shared presets folders and the override dir for one test."""
    monkeypatch.setattr(presets, "shared_preset_folders", REAL_SHARED_PRESET_FOLDERS)
    monkeypatch.setattr(paths, "user_presets_dir", REAL_USER_PRESETS_DIR)


@pytest.fixture
def real_shipped_presets(monkeypatch: pytest.MonkeyPatch, _no_user_presets: None) -> None:
    """Undo the empty package layer for one test (the path of the package file itself)."""
    monkeypatch.setattr(paths, "shipped_presets", REAL_SHIPPED_PRESETS)
