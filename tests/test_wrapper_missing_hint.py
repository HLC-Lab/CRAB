"""When a wrapper is not found and the checkout has no `wrappers/` folder (a plain `git pull`
removed the tracked files), the error says to run `crab update`. With a checkout, or with
CRAB_PATH_WRAPPERS set, the messages stay as they were."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import crab.core.wrapper_paths as wp
from crab.core.experiment.runner import ExperimentRunner

URL = "https://github.com/HLC-Lab/crab-wrappers"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    return tmp_path


def test_no_wrappers_folder_gives_the_hint(root: Path) -> None:
    hint = wp.missing_wrappers_hint()
    assert "wrappers/ is missing" in hint
    assert "crab update" in hint
    assert URL in hint


def test_a_wrappers_folder_gives_no_hint(root: Path) -> None:
    (root / "wrappers" / "blink").mkdir(parents=True)
    (root / "wrappers" / "blink" / "x.py").write_text("")
    assert wp.missing_wrappers_hint() == ""


def test_a_configured_folder_gives_no_hint(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(root / "mine"))
    assert wp.missing_wrappers_hint() == ""


def test_leftovers_of_an_untracked_checkout_count_as_missing(root: Path) -> None:
    w = root / "wrappers"
    (w / "blink" / "__pycache__").mkdir(parents=True)
    (w / "blink" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"")
    (w / "stray.pyc").write_bytes(b"")
    (w / ".pytest_cache").mkdir()
    (w / ".pytest_cache" / "README.md").write_text("")
    (w / ".ruff_cache").mkdir()
    (w / ".ruff_cache" / "x").write_text("")
    assert "crab update" in wp.missing_wrappers_hint()


@pytest.mark.parametrize("name", ["blink/x.py", ".git/HEAD"])
def test_any_other_file_counts_as_present(root: Path, name: str) -> None:
    f = root / "wrappers" / name
    f.parent.mkdir(parents=True)
    (f.parent / "__pycache__").mkdir()
    f.write_text("")
    assert wp.missing_wrappers_hint() == ""


def _parse(monkeypatch: pytest.MonkeyPatch, capsys, root: Path) -> tuple[int, str]:
    from crab.cli.main import cli_router

    out = root / "out.txt"
    out.write_text("1\n")
    monkeypatch.setattr(sys, "argv", ["crab", "parse", "blink/nope.py", str(out)])
    try:
        cli_router()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, capsys.readouterr().err


def test_crab_parse_without_a_checkout_says_how_to_get_it(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    code, err = _parse(monkeypatch, capsys, root)
    assert code != 0
    assert "wrapper not found" in err
    assert "crab update" in err and URL in err


def test_crab_parse_with_a_checkout_keeps_the_old_message(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    (root / "wrappers" / "blink").mkdir(parents=True)
    (root / "wrappers" / "blink" / "x.py").write_text("")
    code, err = _parse(monkeypatch, capsys, root)
    assert code != 0
    assert "wrapper not found: blink/nope.py" in err
    assert "crab update" not in err


def _runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExperimentRunner:
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    return ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": "blink/nope.py", "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
    )


def test_the_runner_error_includes_the_hint_without_a_checkout(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner(root, monkeypatch)
    with pytest.raises(FileNotFoundError) as exc:
        runner.setup()
    msg = str(exc.value)
    assert msg.startswith("Wrapper not found: blink/nope.py")
    assert "crab update" in msg and URL in msg


def test_the_runner_error_is_unchanged_with_a_checkout(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / "wrappers" / "blink").mkdir(parents=True)
    (root / "wrappers" / "blink" / "x.py").write_text("")
    runner = _runner(root, monkeypatch)
    with pytest.raises(FileNotFoundError) as exc:
        runner.setup()
    assert str(exc.value) == "Wrapper not found: blink/nope.py"
