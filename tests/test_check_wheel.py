"""scripts/check_wheel.py: the wheel contents check, on fabricated zip files."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_wheel.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_wheel", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_wheel = _load()

REQUIRED = [
    "crab/web/static/index.html",
    "crab/crab_dashboard.html",
    "crab/wrappers/base.py",
    "crab/setup/recipes/__init__.py",
    "crab/presets.json",
]


def _wheel(tmp_path: Path, members: list[str]) -> Path:
    path = tmp_path / "crab-0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name in members:
            zf.writestr(name, "")
    return path


def test_complete_wheel_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = _wheel(tmp_path, [*REQUIRED, "crab/__init__.py", "crab-0.dist-info/METADATA"])
    assert check_wheel.main([str(wheel)]) == 0
    assert "problem" not in capsys.readouterr().out.lower()


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_member_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    wheel = _wheel(tmp_path, [m for m in REQUIRED if m != missing])
    assert check_wheel.main([str(wheel)]) == 1
    out = capsys.readouterr().out
    assert missing in out
    for other in REQUIRED:
        if other != missing:
            assert other not in out


@pytest.mark.parametrize(
    "stray",
    [
        "crab/webui/node_modules/flatted/python/flatted.py",
        "tests/test_x.py",
        "crab/tests/test_x.py",
        "examples/nplb/run.yaml",
    ],
)
def test_forbidden_member_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stray: str
) -> None:
    wheel = _wheel(tmp_path, [*REQUIRED, stray])
    assert check_wheel.main([str(wheel)]) == 1
    assert stray in capsys.readouterr().out


def test_lookalike_names_are_not_forbidden(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, [*REQUIRED, "crab/core/contests/a.py", "crab/my_examples.py"])
    assert check_wheel.main([str(wheel)]) == 0


def test_several_problems_are_listed_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    members = [m for m in REQUIRED if m not in REQUIRED[:2]]
    wheel = _wheel(tmp_path, [*members, "node_modules/x.py", "examples/a.yaml"])
    assert check_wheel.main([str(wheel)]) == 1
    out = capsys.readouterr().out
    for expected in [*REQUIRED[:2], "node_modules/x.py", "examples/a.yaml"]:
        assert expected in out


def test_unreadable_wheel_is_an_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.whl"
    bad.write_text("not a zip")
    assert check_wheel.main([str(bad)]) == 1
    assert check_wheel.main([str(tmp_path / "absent.whl")]) == 1


def test_usage_without_arguments() -> None:
    assert check_wheel.main([]) == 2
