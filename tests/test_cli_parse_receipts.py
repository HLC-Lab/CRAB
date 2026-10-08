"""`crab parse` runs a wrapper's parser on saved output (the path partners' sample tests and
SbatchMan's parser use), and `crab receipts set` registers a binary without the wizard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = """from crab.wrappers.base import base


class app(base):
    keys = ["size"]
    metadata = [{"name": "lat", "unit": "us", "conv": True}]

    def read_data(self):
        rows = []
        for line in self.stdout.splitlines():
            size, lat = line.split()
            rows.append({"size": int(size), "lat": float(lat) * self.scale})
        return rows
"""


def _crab(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from crab.cli.main import cli_router; cli_router()", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    (tmp_path / "w.py").write_text(WRAPPER)
    case = tmp_path / "case"
    case.mkdir()
    (case / "stdout.txt").write_text("8 1.5\n16 2.5\n")
    return case


def test_parse_prints_rows_as_json(tmp_path: Path, sample: Path) -> None:
    out = _crab(
        "parse", str(tmp_path / "w.py"), str(sample / "stdout.txt"), "--set", "scale=2", "--json"
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {
        "rows": [{"size": 8, "lat": 3.0}, {"size": 16, "lat": 5.0}],
        "legacy_shape": False,
    }


def test_parse_prints_csv_by_default(tmp_path: Path, sample: Path) -> None:
    out = _crab("parse", str(tmp_path / "w.py"), str(sample / "stdout.txt"), "--set", "scale=1")
    assert out.stdout.splitlines() == ["size,lat", "8,1.5", "16,2.5"]


def test_check_passes_on_matching_expected_values(tmp_path: Path, sample: Path) -> None:
    expected = sample / "expected.json"
    expected.write_text(json.dumps([{"size": 8, "lat": 1.5}, {"size": 16, "lat": 2.5}]))
    out = _crab(
        "parse",
        str(tmp_path / "w.py"),
        str(sample / "stdout.txt"),
        "--set",
        "scale=1",
        "--check",
        str(expected),
    )
    assert out.returncode == 0, out.stderr


def test_check_fails_and_names_the_difference(tmp_path: Path, sample: Path) -> None:
    expected = sample / "expected.json"
    expected.write_text(json.dumps([{"size": 8, "lat": 1.5}, {"size": 16, "lat": 9.9}]))
    out = _crab(
        "parse",
        str(tmp_path / "w.py"),
        str(sample / "stdout.txt"),
        "--set",
        "scale=1",
        "--check",
        str(expected),
    )
    assert out.returncode == 1
    assert "row 1" in out.stderr and "9.9" in out.stderr


def test_a_parse_error_exits_2_with_the_reason(tmp_path: Path, sample: Path) -> None:
    (sample / "stdout.txt").write_text("garbage\n")
    out = _crab("parse", str(tmp_path / "w.py"), str(sample / "stdout.txt"), "--set", "scale=1")
    assert out.returncode == 2
    assert "read_data raised" in out.stderr


def _crab_inprocess(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    """The real CLI entry point, in-process, so the receipts folder can be redirected."""
    from crab.cli.main import cli_router

    monkeypatch.setattr(sys, "argv", ["crab", *args])
    try:
        cli_router()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import crab.setup.memory as mem

    local = tmp_path / "receipts"
    monkeypatch.setattr(mem, "ENV_DIR", str(local))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(tmp_path / "legacy"))
    return local


def test_receipts_set_writes_a_local_receipt(tmp_path, receipts, monkeypatch, capsys) -> None:
    binary = tmp_path / "xhpl"
    binary.write_text("#!/bin/sh\n")
    code, out, err = _crab_inprocess(
        monkeypatch,
        capsys,
        "receipts",
        "set",
        "hpl",
        "--binary",
        str(binary),
        "--pre-run",
        "module load mpi",
        "--launcher",
        "srun",
        "--json",
    )
    assert code == 0, err
    saved = json.loads((receipts / "hpl.json").read_text())
    assert saved == {
        "id": "hpl",
        "type": "binary",
        "binary_path": str(binary),
        "launcher_override": "srun",
        "hooks": {"pre_run": ["module load mpi"], "post_run": []},
    }
    assert json.loads(out)["receipt"] == saved


def test_receipts_set_refuses_a_missing_binary(tmp_path, receipts, monkeypatch, capsys) -> None:
    code, _, err = _crab_inprocess(
        monkeypatch, capsys, "receipts", "set", "hpl", "--binary", str(tmp_path / "nope")
    )
    assert code == 2
    assert "does not exist" in err
    assert not (receipts / "hpl.json").exists()


def test_receipts_set_refuses_a_launcher_path(tmp_path, receipts, monkeypatch, capsys) -> None:
    binary = tmp_path / "xhpl"
    binary.write_text("#!/bin/sh\n")
    code, _, err = _crab_inprocess(
        monkeypatch,
        capsys,
        "receipts",
        "set",
        "hpl",
        "--binary",
        str(binary),
        "--launcher",
        "/opt/x/mpirun",
    )
    assert code == 2
    assert "srun" in err and "mpirun" in err
    assert not (receipts / "hpl.json").exists()


def test_receipts_set_saves_mpirun_launcher(tmp_path, receipts, monkeypatch, capsys) -> None:
    binary = tmp_path / "xhpl"
    binary.write_text("#!/bin/sh\n")
    code, _, err = _crab_inprocess(
        monkeypatch,
        capsys,
        "receipts",
        "set",
        "hpl",
        "--binary",
        str(binary),
        "--launcher",
        "mpirun",
    )
    assert code == 0, err
    assert json.loads((receipts / "hpl.json").read_text())["launcher_override"] == "mpirun"


def test_receipts_set_without_launcher_saves_no_override(
    tmp_path, receipts, monkeypatch, capsys
) -> None:
    binary = tmp_path / "xhpl"
    binary.write_text("#!/bin/sh\n")
    code, _, err = _crab_inprocess(
        monkeypatch, capsys, "receipts", "set", "hpl", "--binary", str(binary)
    )
    assert code == 0, err
    assert json.loads((receipts / "hpl.json").read_text())["launcher_override"] == ""
