"""Receipts are written to the untracked `local/receipts/`; receipts from before that folder
existed (`config/environments/`) are still read, and a local receipt wins over an old one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import crab.setup.memory as mem
from crab.cli import contract


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    local, legacy = tmp_path / "local" / "receipts", tmp_path / "config" / "environments"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(mem, "ENV_DIR", str(local))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(legacy))
    monkeypatch.setattr(contract, "_CRAB_ROOT", tmp_path)
    return local, legacy


def _write(d: Path, bench_id: str, binary: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{bench_id}.json").write_text(json.dumps({"id": bench_id, "binary_path": binary}))


def test_new_receipts_are_saved_in_the_local_folder(dirs: tuple[Path, Path]) -> None:
    local, legacy = dirs
    mem.save_receipt("g500", {"id": "g500", "binary_path": "/new"})
    assert (local / "g500.json").exists()
    assert not (legacy / "g500.json").exists()


def test_an_old_receipt_is_still_found(dirs: tuple[Path, Path]) -> None:
    _write(dirs[1], "blink", "/old")
    assert mem.get_receipt("blink")["binary_path"] == "/old"
    assert mem.get_all_receipts()["blink"]["binary_path"] == "/old"


def test_a_local_receipt_wins_over_an_old_one(dirs: tuple[Path, Path]) -> None:
    local, legacy = dirs
    _write(legacy, "blink", "/old")
    _write(local, "blink", "/new")
    assert mem.get_receipt("blink")["binary_path"] == "/new"
    assert mem.get_all_receipts()["blink"]["binary_path"] == "/new"


def test_removing_a_receipt_removes_both_copies(dirs: tuple[Path, Path]) -> None:
    local, legacy = dirs
    _write(legacy, "blink", "/old")
    _write(local, "blink", "/new")
    mem.remove_receipt("blink")
    assert mem.get_receipt("blink") is None


def test_crab_benchmarks_lists_both_folders_local_winning(dirs: tuple[Path, Path]) -> None:
    local, legacy = dirs
    _write(legacy, "blink", "/old")
    _write(legacy, "qe", "/qe")
    _write(local, "blink", "/new")
    got = {
        b["id"]: b["binary_path"]
        for b in contract.gather_benchmarks(wrappers_dir=local)["benchmarks"]
    }
    assert got == {"blink": "/new", "qe": "/qe"}
