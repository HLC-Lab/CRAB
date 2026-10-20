"""The installed layout (`CRAB_HOME` set, not a checkout) keeps every state dir out of site-packages.

Modules copy values from `crab.paths` at import, so the test reloads them under the forced layout
and reloads them again with the real environment at teardown.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

import crab
from crab import paths
from crab.cli import contract
from crab.core import engine, provenance, wrapper_paths
from crab.setup import memory, wizard

PACKAGE_PARENT = Path(crab.__file__).resolve().parents[1]

_MODULES = (paths, engine, wrapper_paths, provenance, memory, wizard, contract)


def _reload_all() -> None:
    importlib.reload(paths)
    for module in _MODULES[1:]:
        importlib.reload(module)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`CRAB_HOME=<tmp>/home` with the installed layout forced and the modules reloaded."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("CRAB_HOME", str(tmp_home))
    monkeypatch.delenv("CRAB_PATH_PRESETS", raising=False)
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    importlib.reload(paths)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    for module in _MODULES[1:]:
        importlib.reload(module)
    yield tmp_home
    monkeypatch.undo()
    _reload_all()


def _under(path: str | Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def _derived_dirs() -> dict[str, str]:
    return {
        "crab_home": str(paths.crab_home()),
        "engine data": str(Path(engine.CRAB_ROOT) / "data"),
        "benchmarks": wizard.BENCHMARKS_DIR,
        "receipts": memory.ENV_DIR,
        "config": memory.CONFIG_DIR,
        "contract root": str(contract._CRAB_ROOT),
        "contract data": str(contract._CRAB_ROOT / "data"),
        "private wrappers": str(paths.local_path(Path(wrapper_paths._CRAB_ROOT), "wrappers")),
        "user presets": str(paths.user_presets()),
        **{
            f"wrapper search {i}": folder
            for i, folder in enumerate(wrapper_paths.wrapper_search_path())
        },
        "provenance wrappers": provenance._wrappers_dir(),
    }


def test_every_derived_dir_lies_under_crab_home(home: Path) -> None:
    assert paths.layout() == "installed"
    outside = {name: p for name, p in _derived_dirs().items() if not _under(p, home)}
    assert outside == {}


def test_no_derived_dir_lies_in_the_package_tree(home: Path) -> None:
    inside = {name: p for name, p in _derived_dirs().items() if _under(p, PACKAGE_PARENT)}
    assert inside == {}


def test_the_reload_leaves_no_trace_after_teardown(tmp_path: Path) -> None:
    """After the fixture is gone the modules point at the real (checkout) home again."""
    assert paths.layout() == "checkout"
    assert engine.CRAB_ROOT == str(paths.crab_home())
