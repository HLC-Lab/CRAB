"""A receipt that is missing is fine (the binary lookup falls through to the config and PATH,
ADR-031). A receipt file that exists but cannot be read or parsed is an error naming the file:
before, it was backed up and treated as missing, so the wrong binary could be used silently."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import crab.setup.memory as mem


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    local, legacy = tmp_path / "local" / "receipts", tmp_path / "config" / "environments"
    local.mkdir(parents=True)
    legacy.mkdir(parents=True)
    monkeypatch.setattr(mem, "ENV_DIR", str(local))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(legacy))
    return local, legacy


def test_a_missing_receipt_is_none(dirs: tuple[Path, Path]) -> None:
    assert mem.get_receipt("nothing") is None


@pytest.mark.parametrize("where", [0, 1], ids=["local", "legacy"])
def test_corrupt_json_raises_and_names_the_file(dirs: tuple[Path, Path], where: int) -> None:
    path = dirs[where] / "blink.json"
    path.write_text('{"id": "blink", "binary_path": ')
    with pytest.raises(mem.ReceiptError) as caught:
        mem.get_receipt("blink")
    assert str(path) in str(caught.value)
    assert "not valid JSON" in str(caught.value)
    assert path.read_text() == '{"id": "blink", "binary_path": '  # left alone for the owner
    assert [p.name for p in dirs[where].iterdir()] == ["blink.json"]


def test_a_receipt_that_is_not_an_object_raises(dirs: tuple[Path, Path]) -> None:
    path = dirs[0] / "blink.json"
    path.write_text("[1, 2]")
    with pytest.raises(mem.ReceiptError, match="JSON object") as caught:
        mem.get_receipt("blink")
    assert str(path) in str(caught.value)


def test_an_empty_receipt_file_raises(dirs: tuple[Path, Path]) -> None:
    (dirs[0] / "blink.json").write_text("")
    with pytest.raises(mem.ReceiptError, match="blink.json"):
        mem.get_receipt("blink")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
def test_an_unreadable_receipt_raises_and_names_the_file(dirs: tuple[Path, Path]) -> None:
    path = dirs[0] / "blink.json"
    path.write_text('{"id": "blink"}')
    path.chmod(0)
    try:
        with pytest.raises(mem.ReceiptError) as caught:
            mem.get_receipt("blink")
    finally:
        path.chmod(0o600)
    assert str(path) in str(caught.value)
    assert "cannot read" in str(caught.value)


def test_a_good_receipt_still_loads(dirs: tuple[Path, Path]) -> None:
    mem.save_receipt("blink", {"id": "blink", "binary_path": "/x"})
    assert mem.get_receipt("blink") == {"id": "blink", "binary_path": "/x"}


def test_listing_all_receipts_fails_on_a_corrupt_one(dirs: tuple[Path, Path]) -> None:
    mem.save_receipt("good", {"id": "good"})
    (dirs[0] / "bad.json").write_text("{nope")
    with pytest.raises(mem.ReceiptError, match="bad.json"):
        mem.get_all_receipts()


# ---- what a user sees: one line naming the file, never a traceback --------------------------


def _corrupt(dirs: tuple[Path, Path]) -> Path:
    path = dirs[0] / "blink.json"
    path.write_text("{nope")
    return path


def _assert_clean_one_line(err: str, path: Path) -> None:
    assert str(path) in err
    assert "not valid JSON" in err
    assert "Traceback" not in err
    assert len([line for line in err.splitlines() if str(path) in line]) == 1


def test_crab_run_stops_with_one_line_naming_the_receipt(
    dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import json
    from unittest.mock import patch

    from crab.cli.orchestrator import execute_orchestrator
    from settings_fixtures import LOCAL_DIRECT

    path = _corrupt(dirs)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    preset = {"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT}
    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset),
        patch("crab.core.engine.Engine.run", return_value={}) as run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_orchestrator(str(config), "lab", as_json=True)
    assert exc.value.code == 1
    run.assert_not_called()
    _assert_clean_one_line(capsys.readouterr().err, path)


def test_crab_list_benchmarks_fails_on_a_corrupt_receipt(
    dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crab.cli import contract

    path = _corrupt(dirs)
    monkeypatch.setattr(contract, "_CRAB_ROOT", tmp_path)
    with pytest.raises(mem.ReceiptError) as caught:
        contract.gather_benchmarks(env_dir=dirs[0], wrappers_dir=tmp_path / "none")
    assert str(path) in str(caught.value)


@pytest.mark.parametrize("command", ["setup", "list-benchmarks"])
def test_the_router_prints_a_receipt_error_as_one_line(
    command: str, dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import sys

    from crab.cli import contract
    from crab.cli.main import cli_router

    path = _corrupt(dirs)
    monkeypatch.setattr(contract, "_CRAB_ROOT", tmp_path)
    monkeypatch.setattr(mem, "CRAB_ROOT", str(tmp_path))
    if command == "setup":
        import crab.setup.wizard as wizard

        def fail(*_a, **_k):
            mem.get_receipt("blink")

        monkeypatch.setattr(wizard, "run", fail)
    else:
        # The listing reads the same folders as the engine: point it at the corrupt local one.
        monkeypatch.setattr(contract.paths, "local_path", lambda _root, _name: dirs[0])
    monkeypatch.setattr(sys, "argv", ["crab", command])
    with pytest.raises(SystemExit) as exc:
        cli_router()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("crab: ")
    _assert_clean_one_line(err, path)
