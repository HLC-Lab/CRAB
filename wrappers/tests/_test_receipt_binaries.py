"""Which binary the Graph500 and blink wrappers launch, for each kind of setup receipt.

A source or binary receipt stores a directory: g500's src/ with graph500_reference_bfs in it,
or blink's bin/ with one executable per microbench. A module receipt stores the executable the
user typed: a command name found on PATH once the module loads, or a path.

The file name starts with `_` so `crab wrappers list` does not take it for a wrapper.
Run with: python -m pytest tests
"""

import importlib.util
from pathlib import Path

import pytest
from crab.setup import memory

ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app(0, False, "")


def _executable(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    """Write a receipt into an isolated receipts folder."""
    folder = tmp_path / "receipts"
    folder.mkdir()
    monkeypatch.setattr(memory, "ENV_DIR", str(folder))
    monkeypatch.setattr(memory, "LEGACY_ENV_DIR", str(folder))

    def write(benchmark_id: str, install_type: str, binary_path: str) -> None:
        memory.save_receipt(
            benchmark_id,
            {"id": benchmark_id, "type": install_type, "binary_path": binary_path},
        )

    return write


# ── Graph500 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("install_type", ["source", "binary"])
def test_g500_directory_receipt_runs_the_bfs_binary_inside(install_type, tmp_path, receipts):
    src = tmp_path / "g500" / "src"
    bfs = _executable(src / "graph500_reference_bfs")
    receipts("g500", install_type, str(src))

    assert _load("graph500/g500_wrapper.py").resolve_binary() == bfs


def test_g500_binary_receipt_pointing_at_the_executable_runs_it(tmp_path, receipts):
    bfs = _executable(tmp_path / "opt" / "graph500_reference_bfs")
    receipts("g500", "binary", bfs)

    assert _load("graph500/g500_wrapper.py").resolve_binary() == bfs


@pytest.mark.parametrize("name", ["graph500_reference_bfs", "g500_bfs"])
def test_g500_module_receipt_with_a_command_name_runs_it_as_is(name, receipts):
    receipts("g500", "module", name)

    assert _load("graph500/g500_wrapper.py").resolve_binary() == name


def test_g500_module_receipt_with_a_path_runs_it_as_is(tmp_path, receipts):
    bfs = _executable(tmp_path / "apps" / "graph500" / "bin" / "graph500_reference_bfs")
    receipts("g500", "module", bfs)

    assert _load("graph500/g500_wrapper.py").resolve_binary() == bfs


# ── blink ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("install_type", ["source", "binary"])
def test_blink_directory_receipt_runs_each_microbench_from_it(install_type, tmp_path, receipts):
    bindir = tmp_path / "blink" / "bin"
    ping = _executable(bindir / "ping-pong_b")
    a2a = _executable(bindir / "a2a_b")
    receipts("blink", install_type, str(bindir))

    assert _load("blink/ping-pong_b.py").resolve_binary() == ping
    assert _load("blink/a2a_b.py").resolve_binary() == a2a


@pytest.mark.parametrize("typed", ["ping-pong_b", "blink"])
def test_blink_module_receipt_with_a_command_name_runs_bare_microbench_names(typed, receipts):
    receipts("blink", "module", typed)

    assert _load("blink/ping-pong_b.py").resolve_binary() == "ping-pong_b"
    assert _load("blink/a2a_b.py").resolve_binary() == "a2a_b"


def test_blink_module_receipt_with_a_path_finds_each_microbench_beside_it(tmp_path, receipts):
    bindir = tmp_path / "apps" / "blink" / "bin"
    ping = _executable(bindir / "ping-pong_b")
    a2a = _executable(bindir / "a2a_b")
    receipts("blink", "module", ping)

    assert _load("blink/ping-pong_b.py").resolve_binary() == ping
    assert _load("blink/a2a_b.py").resolve_binary() == a2a


def test_blink_module_receipt_with_a_directory_runs_each_microbench_from_it(tmp_path, receipts):
    bindir = tmp_path / "apps" / "blink" / "bin"
    a2a = _executable(bindir / "a2a_b")
    receipts("blink", "module", str(bindir))

    assert _load("blink/a2a_b.py").resolve_binary() == a2a
