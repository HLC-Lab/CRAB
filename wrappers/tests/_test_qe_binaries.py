"""Which binary the Quantum ESPRESSO wrappers launch.

pw and ph share one receipt per QE version (qe-v6, qe-v7) that points at pw.x. The ph wrappers
derive ph.x from it: next to pw.x when the receipt gives a directory or a path, or as a bare
`ph.x` when it gives a command name (module installs). An app's `binary` key in the config wins
over the receipt, and run_app launches the same binary the engine's pre-flight checks.

The file name starts with `_` so `crab wrappers list` does not take it for a wrapper.
Run with: python -m pytest tests
"""

import importlib.util
from pathlib import Path

import pytest
from crab.setup import memory
from crab.wrappers.base import MissingBinaryError

QE_DIR = Path(__file__).resolve().parents[1] / "quantum-espresso"
VERSIONS = {"v6": "qe-v6", "v7": "qe-v7"}


def _load(kind: str, version: str):
    path = QE_DIR / kind / f"{version}.py"
    spec = importlib.util.spec_from_file_location(f"qe_{kind}_{version}", path)
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
    """Write a receipt for a QE version into an isolated receipts folder."""
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


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "scf.in"
    path.write_text("&CONTROL\n    outdir = './out'\n/\n")
    return str(path)


def _launched(app, input_file: str, run_dir: Path) -> str:
    """The binary run_app puts at the start of the command."""
    run_dir.mkdir(exist_ok=True)
    app.input_file = input_file
    app.run_dir = str(run_dir)
    return app.run_app().split(" < ")[0]


@pytest.mark.parametrize("version", VERSIONS)
def test_source_install_runs_both_programs_from_the_build_dir(version, tmp_path, receipts):
    target = tmp_path / VERSIONS[version]
    pw = _executable(target / "build" / "bin" / "pw.x")
    ph = _executable(target / "build" / "bin" / "ph.x")
    receipts(VERSIONS[version], "source", str(target / "bin"))

    assert _load("pw", version).resolve_binary() == pw
    assert _load("ph", version).resolve_binary() == ph


@pytest.mark.parametrize("version", VERSIONS)
def test_binary_install_finds_ph_next_to_pw(version, tmp_path, receipts):
    bindir = tmp_path / "qe" / "bin"
    _executable(bindir / "pw.x")
    ph = _executable(bindir / "ph.x")
    receipts(VERSIONS[version], "binary", str(bindir))

    assert _load("ph", version).resolve_binary() == ph


@pytest.mark.parametrize("version", VERSIONS)
def test_module_install_with_a_command_name_runs_ph_x(version, receipts, input_file, tmp_path):
    receipts(VERSIONS[version], "module", "pw.x")

    ph_app = _load("ph", version)
    assert ph_app.resolve_binary() == "ph.x"
    assert _launched(ph_app, input_file, tmp_path / "run") == "ph.x"
    assert _load("pw", version).resolve_binary() == "pw.x"


@pytest.mark.parametrize("version", VERSIONS)
def test_module_install_with_a_path_finds_ph_in_the_same_dir(version, tmp_path, receipts):
    bindir = tmp_path / "opt" / "qe" / "bin"
    pw = _executable(bindir / "pw.x")
    ph = _executable(bindir / "ph.x")
    receipts(VERSIONS[version], "module", pw)

    assert _load("ph", version).resolve_binary() == ph


@pytest.mark.parametrize("kind", ["pw", "ph"])
def test_config_binary_wins_and_is_what_run_app_launches(kind, tmp_path, receipts, input_file):
    bindir = tmp_path / "qe" / "bin"
    _executable(bindir / "pw.x")
    _executable(bindir / "ph.x")
    receipts("qe-v7", "binary", str(bindir))
    app = _load(kind, "v7")
    app.binary = "/custom/build/my-qe.x"

    assert app.resolve_binary() == "/custom/build/my-qe.x"
    assert _launched(app, input_file, tmp_path / "run") == "/custom/build/my-qe.x"


@pytest.mark.parametrize("version", VERSIONS)
def test_missing_ph_next_to_pw_is_a_loud_error(version, tmp_path, receipts, input_file):
    bindir = tmp_path / "qe" / "bin"
    _executable(bindir / "pw.x")
    receipts(VERSIONS[version], "binary", str(bindir))
    app = _load("ph", version)

    with pytest.raises(MissingBinaryError) as excinfo:
        app.resolve_binary()
    message = str(excinfo.value)
    assert "ph.x" in message
    assert str(bindir) in message

    with pytest.raises(MissingBinaryError):
        _launched(app, input_file, tmp_path / "run")


def test_no_receipt_is_a_missing_binary_error(receipts, input_file, tmp_path):
    for kind in ("pw", "ph"):
        app = _load(kind, "v7")
        with pytest.raises(MissingBinaryError):
            _launched(app, input_file, tmp_path / "run")
