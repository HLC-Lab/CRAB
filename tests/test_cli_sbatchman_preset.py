"""`crab sbatchman-preset`: the `sbatchman configure slurm` command for this CRAB install.

`build_configure_command` is a pure function, tested on its text. The CLI tests run the router
in-process (like `tests/test_cli_nodes.py`) with `crab.core.wrapper_paths._CRAB_ROOT` pointed at
a temporary root, so no real checkout is read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from crab.cli.main import cli_router
from crab.cli.sbatchman_preset import build_configs_file, build_configure_command, handle
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


# --- the configs-file form ---------------------------------------------------------------------


def _configs(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "venv_bin": Path("/opt/crab/.venv/bin"),
        "crab_root": Path("/opt/crab"),
        "wrappers_dir": Path("/opt/crab/wrappers"),
        "name": "crab_run",
        "partition": "p",
        "account": "a",
        "qos": None,
        "time": "00:20:00",
        "nodes": "4,8,16",
        "tasks_per_node": "4",
        "system": "leonardo",
        "exclusive": True,
        "cpu_bind": None,
        "produced_by": "crab sbatchman-preset --configs-file --nodes 4,8,16",
    }
    values.update(overrides)
    return build_configs_file(**values)


def _entry(text: str, system: str = "leonardo") -> dict[str, Any]:
    configs = yaml.safe_load(text)[system]["configs"]
    assert len(configs) == 1
    entry: dict[str, Any] = configs[0]
    return entry


def test_configs_file_sweeps_nodes_in_one_config() -> None:
    data = yaml.safe_load(_configs())

    assert set(data) == {"variables", "leonardo"}
    assert data["variables"] == {"nodes": [4, 8, 16]}
    assert data["leonardo"]["scheduler"] == "slurm"
    entry = _entry(_configs())
    assert entry["name"] == "crab_run_{nodes}"
    assert entry["nodes"] == "{nodes}"


def test_configs_file_carries_the_slurm_values_under_sbatchmans_keys() -> None:
    entry = _entry(_configs(qos="dbg"))

    assert entry["partition"] == "p"
    assert entry["account"] == "a"
    assert entry["qos"] == "dbg"
    assert entry["time"] == "00:20:00"
    assert entry["tasks_per_node"] == 4
    assert entry["exclusive"] is True


def test_configs_file_leaves_qos_out_and_drops_exclusive_on_request() -> None:
    entry = _entry(_configs(exclusive=False))

    assert "qos" not in entry
    assert "exclusive" not in entry


def test_configs_file_keeps_placeholders_for_missing_slurm_values() -> None:
    text = _configs(partition=None, account=None, time=None, tasks_per_node=None, system=None)
    entry = _entry(text, system="<cluster name>")

    assert entry["partition"] == "<partition>"
    assert entry["account"] == "<account>"
    assert entry["time"] == "<hh:mm:ss>"
    assert entry["tasks_per_node"] == "<tasks per node>"
    assert "CRAB_SYSTEM=<cluster name>" in entry["env"]


def test_configs_file_env_matches_the_command_forms_env_lines() -> None:
    for cpu_bind in (None, "cores"):
        command = _build(system="leonardo", cpu_bind=cpu_bind)
        from_command = [
            line.strip().removeprefix("--env '").removesuffix("' \\").removesuffix("'")
            for line in _lines(command)
            if line.strip().startswith("--env ")
        ]

        assert _entry(_configs(cpu_bind=cpu_bind))["env"] == from_command


def test_configs_file_env_has_the_job_environment() -> None:
    env = _entry(_configs(cpu_bind="cores"))["env"]

    assert env == [
        "PATH=/opt/crab/.venv/bin:$PATH",
        "CRAB_ROOT=/opt/crab",
        "CRAB_SYSTEM=leonardo",
        "CRAB_PATH_WRAPPERS=/opt/crab/wrappers",
        "SBATCHMAN_JOB_DIR={EXP_DIR}",
        "SLURM_CPU_BIND=cores",
    ]


def test_configs_file_takes_a_single_node_count() -> None:
    assert yaml.safe_load(_configs(nodes="2"))["variables"] == {"nodes": [2]}


def test_configs_file_name_gets_the_nodes_suffix() -> None:
    assert _entry(_configs(name="foo"))["name"] == "foo_{nodes}"


def test_configs_file_header_names_the_command_and_the_cluster_name_rule() -> None:
    text = _configs()
    lines = _lines(text)

    assert lines[0] == "# Generated by: crab sbatchman-preset --configs-file --nodes 4,8,16"
    assert "sbatchman set-cluster-name" in lines[1]
    assert "leonardo" in lines[1]
    assert lines[2].startswith("variables:")
    assert not text.endswith("\n")


def test_configs_file_without_nodes_is_an_error_naming_the_flag() -> None:
    with pytest.raises(ValueError, match="--nodes"):
        _configs(nodes=None)


@pytest.mark.parametrize("nodes", ["a", "4,", ",4", "4,,8", "0", "-2", "4 8", "2.5"])
def test_configs_file_rejects_a_node_list_that_is_not_positive_integers(nodes: str) -> None:
    with pytest.raises(ValueError, match="--nodes"):
        _configs(nodes=nodes)


def test_the_command_form_refuses_a_node_list_and_points_at_configs_file() -> None:
    with pytest.raises(ValueError, match="--configs-file"):
        _build(nodes="4,8")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("system", "my cluster"),
        ("name", "a'b"),
        ("partition", "a b"),
        ("account", "x;rm"),
        ("qos", "$(id)"),
        ("cpu_bind", "cores'"),
        ("crab_root", Path("/opt/it's")),
    ],
)
def test_configs_file_rejects_the_same_unsafe_values(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="one token|single quote"):
        _configs(**{field: value})


def _namespace(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "name": "crab_run",
        "partition": "p",
        "account": "a",
        "qos": None,
        "time": "00:20:00",
        "nodes": "4,8,16",
        "tasks_per_node": "4",
        "system": "leonardo",
        "exclusive": True,
        "cpu_bind": None,
        "configs_file": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_the_handler_prints_the_configs_yaml_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["crab", "sbatchman-preset", "--configs-file", "--nodes", "4,8,16", "--system", "leonardo"],
    )
    handle(_namespace())
    out = capsys.readouterr()

    data = yaml.safe_load(out.out)
    env = data["leonardo"]["configs"][0]["env"]
    venv_bin = Path(sys.executable).parent.absolute()
    assert data["variables"] == {"nodes": [4, 8, 16]}
    assert f"PATH={venv_bin}:$PATH" in env
    assert f"CRAB_ROOT={fake_root}" in env
    assert f"CRAB_PATH_WRAPPERS={fake_root / 'wrappers'}" in env
    assert out.out.startswith(
        "# Generated by: crab sbatchman-preset --configs-file --nodes 4,8,16 --system leonardo\n"
    )
    assert out.err == ""


def test_the_handler_exits_2_on_a_missing_nodes_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["crab", "sbatchman-preset", "--configs-file"])
    with pytest.raises(SystemExit) as exc:
        handle(_namespace(nodes=None))
    out = capsys.readouterr()

    assert exc.value.code == 2
    assert out.out == ""
    assert len(out.err.strip().splitlines()) == 1
    assert "--nodes" in out.err


def test_the_handler_without_the_flag_prints_the_command(
    capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    handle(_namespace(configs_file=False, nodes="2"))

    assert capsys.readouterr().out.startswith("sbatchman configure slurm \\\n")


def test_the_handler_refuses_a_node_list_without_the_flag(
    capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    with pytest.raises(SystemExit) as exc:
        handle(_namespace(configs_file=False, nodes="4,8"))
    out = capsys.readouterr()

    assert exc.value.code == 2
    assert out.out == ""
    assert "--configs-file" in out.err


def test_the_handler_treats_a_namespace_without_the_flag_as_the_command_form(
    capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    ns = _namespace(nodes="2")
    del ns.configs_file
    handle(ns)

    assert capsys.readouterr().out.startswith("sbatchman configure slurm \\\n")


def test_the_handler_reports_a_relative_wrappers_path_as_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "rel/w")
    with pytest.raises(SystemExit) as exc:
        handle(_namespace(configs_file=False, nodes="2"))
    out = capsys.readouterr()

    assert exc.value.code == 2
    assert out.out == ""
    lines = out.err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("crab sbatchman-preset: ")
    assert "CRAB_PATH_WRAPPERS" in lines[0]


def test_the_handler_keeps_an_absolute_wrappers_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "/abs/w")

    handle(_namespace(configs_file=False, nodes="2"))

    assert "  --env 'CRAB_PATH_WRAPPERS=/abs/w' \\" in capsys.readouterr().out


def test_the_handler_defaults_to_the_checkout_wrappers_when_the_variable_is_unset(
    capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    handle(_namespace(configs_file=False, nodes="2"))

    assert f"  --env 'CRAB_PATH_WRAPPERS={fake_root / 'wrappers'}' \\" in capsys.readouterr().out


@pytest.mark.parametrize("system", ["on", "yes", "7up", "a.b"])
def test_configs_file_keeps_a_cluster_name_yaml_would_misread_as_a_string_key(
    system: str,
) -> None:
    data = yaml.safe_load(_configs(system=system))

    assert list(data) == ["variables", system]


def test_configs_file_rejects_a_cluster_named_like_the_variables_block() -> None:
    with pytest.raises(ValueError, match="--system"):
        _configs(system="variables")


# --- the configs-file form through the argument parser ------------------------------------------


def test_the_cli_prints_the_configs_file_for_a_node_sweep(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    _crab(
        monkeypatch,
        "sbatchman-preset",
        "--configs-file",
        "--nodes",
        "4,8",
        "--system",
        "leonardo",
        "--partition",
        "p",
        "--account",
        "a",
        "--time",
        "00:20:00",
        "--tasks-per-node",
        "4",
    )
    out = capsys.readouterr()

    data = yaml.safe_load(out.out)
    assert data["variables"]["nodes"] == [4, 8]
    assert "leonardo" in data
    assert out.err == ""


def test_the_cli_configs_file_without_nodes_names_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_root: Path
) -> None:
    with pytest.raises(SystemExit) as exc:
        _crab(monkeypatch, "sbatchman-preset", "--configs-file", "--system", "leonardo")
    out = capsys.readouterr()

    assert exc.value.code not in (0, None)
    assert out.out == ""
    assert "--nodes" in out.err


def test_the_cli_help_lists_the_configs_file_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _crab(monkeypatch, "sbatchman-preset", "--help")
    out = capsys.readouterr()

    assert exc.value.code == 0
    assert "--configs-file" in out.out
