"""A config naming a wrapper that is not on the search path is refused before anything runs, and
a pre-rename Blink name says what it became (wrappers/blink/README.md has the table)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import crab.core.wrapper_paths as wp
from crab.cli.orchestrator import execute_orchestrator
from crab.cli.validate import check_config_problems
from crab.core.execution.settings import SLURM_DEFAULT
from crab.core.experiment.runner import ExperimentRunner


@pytest.fixture
def wrappers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A wrapper search path of one folder, holding the renamed Blink wrappers' new homes."""
    folder = tmp_path / "ws"
    for name in ("alltoall/alltoall_b.py", "incast/incast_nb.py", "alltoall/alltoall_nb.py"):
        target = folder / "blink" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("class app: pass\n")
    monkeypatch.setattr(wp, "_CRAB_ROOT", str(tmp_path / "crab-root"))
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(folder))
    return folder


def _config(*paths: str) -> dict:
    apps = {str(i): {"path": p} for i, p in enumerate(paths)}
    return {
        "global_options": {"numnodes": "1", "ppn": "1"},
        "experiments": {"e1": {"apps": apps}},
    }


@pytest.mark.parametrize("old", sorted(k for k, v in wp._RENAMED.items() if v is not None))
def test_every_renamed_wrapper_names_its_new_path(old: str) -> None:
    new = wp._RENAMED[old]
    assert new is not None
    note = wp.renamed_wrapper_note(old)
    assert note is not None and old in note and new in note


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("blink/a2a_b.py", "blink/alltoall/alltoall_b.py"),
        ("blink/agtr_comm_only.py", "blink/allgather/allgather_comm_only.py"),
        ("blink/ardc_nb.py", "blink/allreduce/allreduce_nb.py"),
    ],
)
def test_spot_checks_of_the_readme_table(old: str, new: str) -> None:
    assert new in (wp.renamed_wrapper_note(old) or "")


@pytest.mark.parametrize(
    ("old", "replacement"),
    [
        ("blink/bursty_noise_incast.py", "blink/incast/incast_nb.py"),
        ("blink/bursty_noise_a2a.py", "blink/alltoall/alltoall_nb.py"),
    ],
)
def test_a_removed_bursty_wrapper_names_its_replacement_and_the_burst_flags(
    old: str, replacement: str
) -> None:
    note = wp.renamed_wrapper_note(old)
    assert note is not None
    assert "removed" in note
    assert replacement in note
    assert "-endl -blength <length> -bpause <pause>" in note


@pytest.mark.parametrize("path", ["blink/alltoall/alltoall_b.py", "blink/ring_nb.py", "x/y.py"])
def test_a_current_or_unknown_path_has_no_rename_note(path: str) -> None:
    assert wp.renamed_wrapper_note(path) is None


def test_the_map_has_the_readme_rows_and_the_two_removed_wrappers() -> None:
    renamed = [k for k, v in wp._RENAMED.items() if v is not None]
    removed = [k for k, v in wp._RENAMED.items() if v is None]
    assert len(renamed) == 19
    assert sorted(removed) == ["blink/bursty_noise_a2a.py", "blink/bursty_noise_incast.py"]
    assert set(removed) == set(wp._REMOVED_NOTES)


def test_a_path_that_resolves_has_no_problem(wrappers: Path, tmp_path: Path) -> None:
    absolute = tmp_path / "abs.py"
    absolute.write_text("")
    assert wp.wrapper_path_problem("blink/alltoall/alltoall_b.py") is None  # direct hit
    assert wp.wrapper_path_problem("blink/alltoall_b.py") is None  # moved match
    assert wp.wrapper_path_problem(str(absolute)) is None


def test_a_renamed_name_gets_the_search_path_and_the_rename_note(wrappers: Path) -> None:
    problem = wp.wrapper_path_problem("blink/a2a_b.py")
    assert problem is not None
    assert problem.startswith("wrapper blink/a2a_b.py not found on the wrapper search path (")
    assert str(wrappers) in problem
    assert "blink/alltoall/alltoall_b.py" in problem


def test_an_unknown_name_gets_the_missing_checkout_hint_when_there_is_one(
    wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = wp.wrapper_path_problem("blink/nope.py")
    assert plain is not None and plain.endswith(f"({', '.join(wp.wrapper_search_path())})")

    monkeypatch.setattr(wp, "missing_wrappers_hint", lambda: "wrappers/ is missing: run X")
    assert wp.wrapper_path_problem("blink/nope.py").endswith(". wrappers/ is missing: run X")


def test_a_missing_absolute_path_is_a_problem(wrappers: Path, tmp_path: Path) -> None:
    problem = wp.wrapper_path_problem(str(tmp_path / "gone.py"))
    assert problem is not None and "gone.py" in problem


def test_placeholder_paths_are_skipped(wrappers: Path) -> None:
    assert wp.config_wrapper_path_problems(_config("blink/{name}.py")) == []


def test_only_limits_the_check_to_the_selected_experiments(wrappers: Path) -> None:
    config = _config("blink/a2a_b.py")
    config["experiments"]["e2"] = {"apps": {"0": {"path": "blink/alltoall/alltoall_b.py"}}}
    assert wp.config_wrapper_path_problems(config, only=["e2"]) == []
    assert len(wp.config_wrapper_path_problems(config, only=["e1"])) == 1


def test_the_legacy_applications_form_is_checked(wrappers: Path) -> None:
    config = {"global_options": {}, "applications": {"0": {"path": "blink/a2a_b.py"}}}
    assert len(wp.config_wrapper_path_problems(config)) == 1


def test_check_config_problems_stays_free_of_wrapper_path_errors(wrappers: Path) -> None:
    errors, _warnings = check_config_problems(_config("blink/a2a_b.py"))
    assert errors == []


def _preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.chdir(tmp_path)
    return {"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT}


def test_crab_run_refuses_a_renamed_wrapper_before_the_engine_starts(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    preset = _preset(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config("blink/a2a_b.py", "blink/bursty_noise_incast.py")))

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_orchestrator(str(config_path), "lab", as_json=True)

    assert exc.value.code == 1
    mock_run.assert_not_called()
    err = capsys.readouterr().err
    assert "blink/alltoall/alltoall_b.py" in err
    assert "blink/incast/incast_nb.py" in err
    assert "Traceback" not in err


def test_crab_run_goes_on_when_every_wrapper_resolves(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preset = _preset(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config("blink/alltoall/alltoall_b.py")))

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(config_path), "lab", as_json=True)

    mock_run.assert_called_once()


def test_the_runner_stops_with_the_rename_message(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`crab worker` has no orchestrator check: the loader raises the same message."""
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": "blink/a2a_b.py", "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
    )
    with pytest.raises(FileNotFoundError) as exc:
        runner.setup()
    assert str(exc.value) == wp.wrapper_path_problem("blink/a2a_b.py")
    assert "blink/alltoall/alltoall_b.py" in str(exc.value)


def _run_with_preset_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preset_env: dict, config: dict
) -> MagicMock:
    preset = {**_preset(tmp_path, monkeypatch), "env": preset_env}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(config_path), "lab", as_json=True)
    return mock_run


def test_a_wrapper_folder_set_by_the_preset_env_is_not_refused(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job's search path comes from the preset's env, which wins over this process's."""
    preset_folder = tmp_path / "preset-wrappers"
    (preset_folder / "blink").mkdir(parents=True)
    (preset_folder / "blink" / "only_here.py").write_text("class app: pass\n")
    config = _config("blink/only_here.py")

    mock_run = _run_with_preset_env(
        tmp_path, monkeypatch, {"CRAB_PATH_WRAPPERS": str(preset_folder)}, config
    )

    mock_run.assert_called_once()
    assert os.environ["CRAB_PATH_WRAPPERS"] == str(wrappers)  # restored


def test_the_preset_env_folder_may_use_a_bash_variable_that_this_process_knows(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "base" / "blink").mkdir(parents=True)
    (tmp_path / "base" / "blink" / "only_here.py").write_text("class app: pass\n")
    monkeypatch.setenv("WRAPPER_BASE", str(tmp_path / "base"))

    mock_run = _run_with_preset_env(
        tmp_path,
        monkeypatch,
        {"CRAB_PATH_WRAPPERS": "$WRAPPER_BASE"},
        _config("blink/only_here.py"),
    )

    mock_run.assert_called_once()


def test_an_unexpandable_preset_env_folder_skips_the_check(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_SUCH_VARIABLE_FOR_CRAB", raising=False)

    mock_run = _run_with_preset_env(
        tmp_path,
        monkeypatch,
        {"CRAB_PATH_WRAPPERS": "$NO_SUCH_VARIABLE_FOR_CRAB/w"},
        _config("blink/a2a_b.py"),
    )

    mock_run.assert_called_once()


def test_a_preset_env_folder_without_the_wrapper_is_still_refused(
    wrappers: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-wrappers"
    empty.mkdir()
    with pytest.raises(SystemExit):
        _run_with_preset_env(
            tmp_path,
            monkeypatch,
            {"CRAB_PATH_WRAPPERS": str(empty)},
            _config("blink/alltoall/alltoall_b.py"),  # exists in the process folder only
        )
