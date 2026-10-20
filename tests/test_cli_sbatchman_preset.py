"""`crab sbatchman-preset`: the `sbatchman configure slurm` command for this CRAB install.

`build_configure_command` is a pure function, tested on its text. The CLI tests run the router
in-process (like `tests/test_cli_nodes.py`) with `crab.core.wrapper_paths._CRAB_ROOT` pointed at
a temporary root, so no real checkout is read.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from crab.cli.main import cli_router
from crab.cli.sbatchman_preset import build_configure_command
from crab.core import wrapper_paths


def _build(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "venv_bin": Path("/opt/crab/.venv/bin"),
        "crab_root": Path("/opt/crab"),
        "wrappers_dir": Path("/opt/crab/wrappers"),
        "name": "crab_run",
        "partition": None,
        "account": None,
        "qos": None,
        "time": None,
        "nodes": None,
        "tasks_per_node": None,
        "system": None,
        "exclusive": True,
        "cpu_bind": None,
    }
    values.update(overrides)
    return build_configure_command(**values)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def test_defaults_keep_the_placeholders_and_leave_qos_out() -> None:
    text = _build()
    lines = _lines(text)

    assert lines[0] == "sbatchman configure slurm \\"
    assert "  --name crab_run \\" in lines
    assert "  --partition <partition> \\" in lines
    assert "  --account <account> \\" in lines
    assert "  --time <hh:mm:ss> \\" in lines
    assert "  --nodes <nodes> \\" in lines
    assert "  --tasks-per-node <tasks per node> \\" in lines
    assert "  --exclusive \\" in lines
    assert "--qos" not in text


def test_env_lines_come_in_the_guides_order_and_are_single_quoted() -> None:
    lines = _lines(_build())

    assert lines[-5:] == [
        "  --env 'PATH=/opt/crab/.venv/bin:$PATH' \\",
        "  --env 'CRAB_ROOT=/opt/crab' \\",
        "  --env 'CRAB_SYSTEM=<cluster name>' \\",
        "  --env 'CRAB_PATH_WRAPPERS=/opt/crab/wrappers' \\",
        "  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}'",
    ]
    assert all(line.endswith(" \\") for line in lines[:-1])
    assert not lines[-1].endswith("\\")


def test_values_replace_the_placeholders() -> None:
    text = _build(
        partition="boost",
        account="ACC",
        qos="dbg",
        time="00:30:00",
        nodes="2",
        tasks_per_node="8",
        system="leo",
        name="myrun",
    )

    assert "  --name myrun \\" in _lines(text)
    assert "  --partition boost \\" in _lines(text)
    assert "  --account ACC \\" in _lines(text)
    assert "  --qos dbg \\" in _lines(text)
    assert "  --time 00:30:00 \\" in _lines(text)
    assert "  --nodes 2 \\" in _lines(text)
    assert "  --tasks-per-node 8 \\" in _lines(text)
    assert "  --env 'CRAB_SYSTEM=leo' \\" in _lines(text)
    assert "<" not in text


def test_qos_sits_between_account_and_time() -> None:
    lines = _lines(_build(qos="dbg"))

    assert lines.index("  --qos dbg \\") == lines.index("  --account <account> \\") + 1


def test_cpu_bind_adds_a_final_env_line() -> None:
    lines = _lines(_build(cpu_bind="cores"))

    assert lines[-1] == "  --env 'SLURM_CPU_BIND=cores'"
    assert lines[-2] == "  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}' \\"
    assert "SLURM_CPU_BIND" not in _build()


def test_no_exclusive_drops_the_exclusive_line() -> None:
    assert "--exclusive" not in _build(exclusive=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("system", "it's"),
        ("system", "my cluster"),
        ("name", "my run"),
        ("name", "a'b"),
        ("partition", "a b"),
        ("account", "x;rm"),
        ("qos", "$(id)"),
        ("cpu_bind", "cores'"),
        ("crab_root", Path("/opt/it's")),
        ("wrappers_dir", Path("/opt/it's/wrappers")),
        ("venv_bin", Path("/opt/it's/bin")),
    ],
)
def test_unsafe_values_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="one token|single quote"):
        _build(**{field: value})


def _crab(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["crab", *args])
    cli_router()


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "crab"
    (root / "wrappers" / "app").mkdir(parents=True)
    (root / "wrappers" / "app" / "w.py").write_text("x = 1\n")
    monkeypatch.setattr(wrapper_paths, "_CRAB_ROOT", str(root))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    return root


def test_the_cli_prints_only_the_command_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    _crab(
        monkeypatch,
        "sbatchman-preset",
        "--partition",
        "boost",
        "--system",
        "leo",
        "--cpu-bind",
        "cores",
    )
    out = capsys.readouterr()

    venv_bin = Path(sys.executable).parent.absolute()
    assert out.out.startswith("sbatchman configure slurm \\\n")
    assert "  --partition boost \\" in out.out
    assert f"  --env 'PATH={venv_bin}:$PATH' \\" in out.out
    assert f"  --env 'CRAB_ROOT={fake_root}' \\" in out.out
    assert "  --env 'CRAB_SYSTEM=leo' \\" in out.out
    assert f"  --env 'CRAB_PATH_WRAPPERS={fake_root / 'wrappers'}' \\" in out.out
    assert out.out.endswith("  --env 'SLURM_CPU_BIND=cores'\n")
    assert out.err == ""


def test_the_cli_honours_crab_path_wrappers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_root: Path,
    tmp_path: Path,
) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(mine))

    _crab(monkeypatch, "sbatchman-preset")
    out = capsys.readouterr()

    assert f"  --env 'CRAB_PATH_WRAPPERS={mine}' \\" in out.out
    assert out.err == ""


def test_the_cli_warns_on_stderr_when_the_wrappers_are_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    monkeypatch.setattr(wrapper_paths, "_CRAB_ROOT", str(root))
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)

    _crab(monkeypatch, "sbatchman-preset")
    out = capsys.readouterr()

    assert f"  --env 'CRAB_PATH_WRAPPERS={root / 'wrappers'}' \\" in out.out
    assert out.err == f"warning: {wrapper_paths.missing_wrappers_hint()}\n"
    assert "crab update" in out.err


def test_the_cli_rejects_a_bad_value_without_printing_a_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    with pytest.raises(SystemExit) as exc:
        _crab(monkeypatch, "sbatchman-preset", "--system", "my cluster")
    out = capsys.readouterr()

    assert exc.value.code not in (0, None)
    assert out.out == ""
    assert len(out.err.strip().splitlines()) == 1
    assert "--system" in out.err
