"""`crab wrappers list|test|new`: see which wrappers exist and whether their binary is found,
run every sample case through the real parser, and scaffold a new wrapper that passes its own
sample test out of the box."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

import crab.core.wrapper_paths as wp
import crab.setup.memory as mem

GOOD = """from crab.wrappers.base import base


class app(base):
    executable = "{exe}"
    metadata = [{{"name": "t", "unit": "s", "conv": True}}]

    def read_data(self):
        return [{{"t": float(x)}} for x in self.stdout.split()]
"""


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    monkeypatch.setattr(mem, "ENV_DIR", str(tmp_path / "local" / "receipts"))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(tmp_path / "legacy"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "onpath"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}/usr/bin:/bin")
    return tmp_path


def _wrapper(root: Path, rel: str, exe: str = "onpath", folder: str = "wrappers") -> Path:
    path = root / folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GOOD.format(exe=exe))
    return path


def _sample(app_dir: Path, case: str, wrapper: str, stdout: str, expected: list) -> None:
    d = app_dir / "samples" / case
    d.mkdir(parents=True)
    (d / "case.json").write_text(json.dumps({"wrapper": wrapper}))
    (d / "stdout.txt").write_text(stdout)
    (d / "expected.json").write_text(json.dumps(expected))


def _crab(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    from crab.cli.main import cli_router

    monkeypatch.setattr(sys, "argv", ["crab", *args])
    try:
        cli_router()
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- list ----------------------------------------------------------------------------------


def test_list_reports_where_each_binary_comes_from(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/found.py", exe="onpath")
    _wrapper(root, "app/nothing.py", exe="not_installed")
    _wrapper(root, "mine/draft.py", folder="local/wrappers")
    broken = root / "wrappers" / "app" / "broken.py"
    broken.write_text("raise ImportError('boom')\n")

    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "list", "--json")
    assert code == 0
    data = json.loads(out)
    by_rel = {w["relpath"]: w for w in data["wrappers"]}
    assert by_rel["app/found.py"]["binary"]["status"] == "path"
    assert by_rel["app/found.py"]["binary"]["path"] == str(root / "bin" / "onpath")
    assert by_rel["app/nothing.py"]["binary"]["status"] == "missing"
    assert by_rel["mine/draft.py"]["folder"] == str(root / "local" / "wrappers")
    assert by_rel["app/broken.py"]["loadable"] is False
    assert "boom" in by_rel["app/broken.py"]["error"]
    assert data["search_path"][0] == str(root / "local" / "wrappers")


def test_list_sees_a_receipt(root, monkeypatch, capsys) -> None:
    path = _wrapper(root, "app/r.py", exe="not_installed")
    path.write_text(
        path.read_text().replace(
            'executable = "not_installed"', 'executable = "not_installed"\n    benchmark_id = "rb"'
        )
    )
    _crab(monkeypatch, capsys, "receipts", "set", "rb", "--binary", "/opt/rb", "--allow-missing")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "list", "--json")
    entry = {w["relpath"]: w for w in json.loads(out)["wrappers"]}["app/r.py"]
    assert entry["binary"] == {"status": "receipt", "path": "/opt/rb"}


# ---- test ----------------------------------------------------------------------------------


def test_samples_pass_and_fail(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    _sample(root / "wrappers" / "app", "good", "w.py", "1.5 2.5", [{"t": 1.5}, {"t": 2.5}])
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test")
    assert code == 0 and "PASS  app/good" in out

    _sample(root / "wrappers" / "app", "bad", "w.py", "1.5", [{"t": 9.0}])
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test", "app")
    assert code == 1
    assert "FAIL  app/bad" in out and "9.0" in out


def test_strict_needs_a_sample_per_wrapper_unless_listed_unverified(
    root, monkeypatch, capsys
) -> None:
    _wrapper(root, "app/w.py")
    _wrapper(root, "app/old.py")
    _sample(root / "wrappers" / "app", "good", "w.py", "1.0", [{"t": 1.0}])
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test", "--strict")
    assert code == 1 and "NO SAMPLE  app/old.py" in out

    (root / "wrappers" / "unverified.txt").write_text("# no real output yet\napp/old.py\n")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test", "--strict")
    assert code == 0 and "UNVERIFIED  app/old.py" in out


def _error_sample(app_dir: Path, case: str, wrapper: str, stdout: str, expected: str) -> Path:
    d = app_dir / "samples" / case
    d.mkdir(parents=True)
    (d / "case.json").write_text(json.dumps({"wrapper": wrapper}))
    (d / "stdout.txt").write_text(stdout)
    (d / "expected_error.txt").write_text(expected)
    return d


def test_a_sample_can_expect_a_parse_error(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    _error_sample(root / "wrappers" / "app", "trunc", "w.py", "oops", "could not convert\n")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test", "--strict")
    assert code == 0 and "PASS  app/trunc" in out


def test_an_expected_error_that_is_not_raised_fails(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    _error_sample(root / "wrappers" / "app", "trunc", "w.py", "1.5", "could not convert")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test")
    assert code == 1
    assert "FAIL  app/trunc" in out and "no parse error" in out and "1.5" in out


def test_an_expected_error_with_another_message_fails(root, monkeypatch, capsys) -> None:
    _wrapper(root, "app/w.py")
    _error_sample(root / "wrappers" / "app", "trunc", "w.py", "oops", "no rows")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test")
    assert code == 1
    assert "FAIL  app/trunc" in out and "'no rows'" in out and "could not convert" in out


def test_an_expected_error_needs_a_parse_error_not_another_exception(
    root, monkeypatch, capsys
) -> None:
    path = _wrapper(root, "app/w.py")
    path.write_text(GOOD.format(exe="onpath") + "\nraise RuntimeError('cannot load')\n")
    _error_sample(root / "wrappers" / "app", "trunc", "w.py", "oops", "cannot load")
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test")
    assert code == 1
    assert "app/trunc" in out and "PASS" not in out and "RuntimeError" in out


@pytest.mark.parametrize("both", [True, False])
def test_a_case_needs_exactly_one_expectation(root, monkeypatch, capsys, both) -> None:
    _wrapper(root, "app/w.py")
    d = _error_sample(root / "wrappers" / "app", "odd", "w.py", "oops", "could not convert")
    if both:
        (d / "expected.json").write_text("[]")
    else:
        (d / "expected_error.txt").unlink()
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test")
    assert code == 1
    assert "ERROR  app/odd" in out and "expected.json" in out and "expected_error.txt" in out


# ---- new -----------------------------------------------------------------------------------


def test_a_scaffolded_wrapper_passes_its_own_sample(root, monkeypatch, capsys) -> None:
    code, out, err = _crab(monkeypatch, capsys, "wrappers", "new", "myapp")
    assert code == 0, err
    made = root / "wrappers" / "myapp"
    assert (made / "myapp.py").exists() and (made / "README.md").exists()
    assert (made / "samples" / "example" / "case.json").exists()

    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "test", "myapp", "--strict")
    assert code == 0 and "PASS  myapp/example" in out


def test_new_can_target_the_local_folder_and_refuses_to_overwrite(
    root, monkeypatch, capsys
) -> None:
    code, _, _ = _crab(monkeypatch, capsys, "wrappers", "new", "draft", "--local")
    assert code == 0
    assert (root / "local" / "wrappers" / "draft" / "draft.py").exists()
    code, _, err = _crab(monkeypatch, capsys, "wrappers", "new", "draft", "--local")
    assert code == 2 and "already exists" in err


def test_list_sees_crab_root_like_a_run_does(root, monkeypatch, capsys) -> None:
    """Runs always have CRAB_ROOT (the presets' _common block sets it), so wrappers that
    build paths from it must not show up as broken in the listing."""
    monkeypatch.delenv("CRAB_ROOT", raising=False)
    path = root / "wrappers" / "old" / "envpath.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import os\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def get_binary_path(self):\n"
        "        return os.environ['CRAB_ROOT'] + '/bin/x'\n"
    )
    code, out, _ = _crab(monkeypatch, capsys, "wrappers", "list", "--json")
    entry = {w["relpath"]: w for w in json.loads(out)["wrappers"]}["old/envpath.py"]
    assert entry["binary"] == {"status": "receipt", "path": f"{root}/bin/x"}
