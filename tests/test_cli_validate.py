"""`crab validate`: the checks `crab run` makes before submitting, without submitting.

The CLI is run in-process like `tests/test_cli_nodes.py`; configs are written to `tmp_path` or
read from `examples/`. The preset loader is patched with real `ExecutionSettings`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from crab.cli.main import cli_router
from crab.core.execution.settings import from_preset

LOADER = "crab.cli.orchestrator.load_environment_config"
EXAMPLE = Path(__file__).parent.parent / "examples" / "local" / "sequential_barrier.json"


@pytest.fixture(autouse=True)
def _wrapper_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The wrappers the configs below name, in a folder of their own as the search path."""
    folder = tmp_path / "wrapper-folder"
    for name in ("a.py", "blink/barrier/barrier_b.py"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("class app: pass\n")
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(folder))
    return folder


def _crab(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["crab", "validate", *args])
    cli_router()


def _config(tmp_path: Path, config: dict[str, Any]) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


def _experiment(**global_options: Any) -> dict[str, Any]:
    """A small config; `global_options` overlays numnodes and ppn."""
    return {
        "global_options": {"numnodes": "1", "ppn": "1", **global_options},
        "experiments": {"e1": {"apps": {"0": {"path": "a.py"}}}},
    }


def _local_preset(hosts: list[str]) -> dict[str, Any]:
    return {
        "env": {},
        "sbatch": [],
        "header": [],
        "settings": from_preset(
            {"scheduler": "local", "launcher": "mpirun", "hosts": hosts}, "lab"
        ),
    }


def test_a_valid_example_is_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _crab(monkeypatch, str(EXAMPLE))

    assert capsys.readouterr().out == f"ok: {EXAMPLE}\n"


def test_an_engine_error_exits_1_with_the_engines_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, _experiment(convergeall="yes"))

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, path)

    assert stop.value.code == 1
    captured = capsys.readouterr()
    assert "experiment e1: convergeall must be true or false, got 'yes'" in captured.err
    assert "ok:" not in captured.out


def test_json_for_a_valid_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _crab(monkeypatch, str(EXAMPLE), "--json")

    assert json.loads(capsys.readouterr().out) == {"ok": True, "errors": [], "warnings": []}


def test_json_for_an_invalid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, _experiment(convergeall="yes"))

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, path, "--json")

    assert stop.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "ok": False,
        "errors": ["experiment e1: convergeall must be true or false, got 'yes'"],
        "warnings": [],
    }


def test_a_preset_dependent_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, _experiment(numnodes="3"))

    # Without a preset the config is fine; the preset has one host.
    _crab(monkeypatch, path)
    capsys.readouterr()
    with patch(LOADER, return_value=_local_preset(["ws1:8"])):
        with pytest.raises(SystemExit) as stop:
            _crab(monkeypatch, path, "-p", "lab")

    assert stop.value.code == 1
    assert "numnodes 3 is more than the preset's 1 host(s)" in capsys.readouterr().err


def test_a_preset_that_fits_is_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, _experiment(numnodes="2"))

    with patch(LOADER, return_value=_local_preset(["ws1:8", "ws2:8"])):
        _crab(monkeypatch, path, "--preset", "lab")

    assert capsys.readouterr().out.startswith("ok: ")


def test_an_unknown_preset_exits_1_with_the_not_found_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, _experiment())

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, path, "-p", "no-such-preset-anywhere")

    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "The preset 'no-such-preset-anywhere' was not found in" in err


def test_warnings_are_printed_and_still_exit_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _experiment()
    config["global_options"]["allocation"] = {"mode": "linear", "split": [50]}
    path = _config(tmp_path, config)

    _crab(monkeypatch, path)

    assert capsys.readouterr().out == (
        f"ok: {path}\nexperiment e1: allocation.split sums to 50; 50% of the nodes stay idle\n"
    )


def test_json_carries_the_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _experiment()
    config["global_options"]["allocation"] = {"mode": "linear", "split": [50]}

    _crab(monkeypatch, _config(tmp_path, config), "--json")

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "errors": [],
        "warnings": ["experiment e1: allocation.split sums to 50; 50% of the nodes stay idle"],
    }


def test_a_newer_schema_version_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config(tmp_path, {**_experiment(), "schema_version": 99})

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, path)

    assert stop.value.code == 1
    assert "schema_version 99" in capsys.readouterr().err


def test_a_missing_file_is_a_one_line_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "nope.json")

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, missing)

    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert missing in err
    assert "Traceback" not in err


def test_a_file_that_is_not_json_is_a_one_line_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json")

    with pytest.raises(SystemExit) as stop:
        _crab(monkeypatch, str(path))

    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "not valid JSON" in err


def test_validate_never_loads_a_scheduler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("crab.core.engine.Engine.run") as run:
        _crab(monkeypatch, str(EXAMPLE))

    run.assert_not_called()


def _stale_config(tmp_path: Path) -> str:
    config = _experiment(numnodes="2")
    config["experiments"]["e1"]["apps"] = {
        "0": {"path": "blink/a2a_b.py"},
        "1": {"path": "blink/bursty_noise_incast.py"},
    }
    return _config(tmp_path, config)


def test_a_renamed_wrapper_is_an_error_with_the_new_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _stale_config(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        _crab(monkeypatch, path)

    err = capsys.readouterr().err
    assert exit_info.value.code == 1
    assert "crab: wrapper blink/a2a_b.py not found on the wrapper search path (" in err
    assert "blink/alltoall/alltoall_b.py" in err
    assert "blink/incast/incast_nb.py" in err


def test_json_lists_one_wrapper_error_per_missing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _stale_config(tmp_path)

    with pytest.raises(SystemExit):
        _crab(monkeypatch, path, "--json")

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert len(report["errors"]) == 2
    assert "blink/alltoall/alltoall_b.py" in report["errors"][0]


def test_a_placeholder_path_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _experiment()
    config["experiments"]["e1"]["apps"] = {"0": {"path": "blink/{name}.py"}}
    path = _config(tmp_path, config)

    _crab(monkeypatch, path)

    assert capsys.readouterr().out == f"ok: {path}\n"


def test_validate_file_names_the_new_home_when_it_exists_on_the_search_path(
    tmp_path: Path, _wrapper_folder: Path
) -> None:
    from crab.cli.validate import validate_file

    new_home = _wrapper_folder / "blink" / "alltoall" / "alltoall_b.py"
    new_home.parent.mkdir(parents=True)
    new_home.write_text("class app: pass\n")
    config = _experiment()
    config["experiments"]["e1"]["apps"] = {"0": {"path": "blink/a2a_b.py"}}

    errors, warnings = validate_file(_config(tmp_path, config))

    assert len(errors) == 1 and "blink/alltoall/alltoall_b.py" in errors[0]
    assert warnings == []
    assert validate_file(str(EXAMPLE)) == ([], [])


def test_a_preset_env_wrapper_folder_applies_with_the_preset_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`crab run -p` searches the preset's CRAB_PATH_WRAPPERS; so must `crab validate -p`."""
    from crab.cli.validate import validate_file

    preset_folder = tmp_path / "preset-wrappers"
    (preset_folder / "blink").mkdir(parents=True)
    (preset_folder / "blink" / "only_here.py").write_text("class app: pass\n")
    config = _experiment(numnodes="2")
    config["experiments"]["e1"]["apps"] = {"0": {"path": "blink/only_here.py"}}
    path = _config(tmp_path, config)
    preset = {**_local_preset(["n0:4", "n1:4"]), "env": {"CRAB_PATH_WRAPPERS": str(preset_folder)}}

    with patch(LOADER, return_value=preset):
        with_preset = validate_file(path, "lab")
    without_preset = validate_file(path)

    assert with_preset == ([], [])
    assert len(without_preset[0]) == 1
    assert "wrapper blink/only_here.py not found" in without_preset[0][0]


def test_a_preset_with_an_unset_mark_is_a_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_preset_dirs: None
) -> None:
    from crab.cli.validate import validate_file

    shared = tmp_path / "shared"
    (shared / "presets").mkdir(parents=True)
    (shared / "presets" / "lab.json").write_text(
        json.dumps({"lab": {"scheduler": "slurm", "sbatch": ["--account=<YOUR_ACCOUNT>"]}})
    )
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{shared}:{tmp_path / 'wrapper-folder'}")
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(tmp_path / "overrides"))

    errors, _ = validate_file(_config(tmp_path, _experiment()), preset="lab")

    assert len(errors) == 1
    assert "Preset 'lab' needs your own value for --account." in errors[0]


@pytest.mark.parametrize(
    ("key", "value"), [("CRAB_PATH_WRAPPERS", "/abs/a:rel/b"), ("CRAB_ROOT", "rel/root")]
)
def test_a_relative_folder_in_the_preset_env_is_an_error_line(
    tmp_path: Path, key: str, value: str
) -> None:
    from crab.cli.validate import validate_file

    presets = {"p": {"scheduler": "slurm", "env": {key: value}}}
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        errors, warnings = validate_file(_config(tmp_path, _experiment()), "p")

    assert len(errors) == 1
    assert "Preset 'p'" in errors[0]
    assert key in errors[0]
    assert "absolute" in errors[0]
    assert warnings == []
