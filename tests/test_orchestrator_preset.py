"""A missing preset must be an error, never a silent fallback.

Since the ``local`` preset skips Slurm, silently defaulting to it meant a
forgotten ``-p`` on a cluster ran benchmarks on the login node.
"""

import json
from unittest.mock import patch

import pytest

from crab.cli.orchestrator import execute_orchestrator, load_environment_config
from crab.core.execution.settings import SLURM_DEFAULT
from settings_fixtures import LOCAL_DIRECT


def test_no_preset_anywhere_exits_with_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CRAB_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))

    with (
        patch("crab.cli.orchestrator.load_environment_config") as mock_load,
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_orchestrator(str(config_path), None, as_json=True)

    assert exc.value.code == 1
    mock_load.assert_not_called()
    mock_run.assert_not_called()
    assert "No preset selected" in capsys.readouterr().err


def test_crab_preset_env_var_still_selects_the_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAB_PRESET", "somecluster")
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT}

    with (
        patch(
            "crab.cli.orchestrator.load_environment_config", return_value=preset_config
        ) as mock_load,
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(str(config_path), None)

    mock_load.assert_called_once_with("somecluster")


def test_orchestrator_passes_the_preset_settings_to_the_engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT}

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset_config),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(config_path), "lab")

    assert mock_run.call_args.kwargs["settings"] is LOCAL_DIRECT


def test_a_preset_without_env_gets_the_engine_default_crab_root() -> None:
    presets = {"lab": {"scheduler": "slurm"}}
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        config = load_environment_config("lab")

    assert config["env"] == {"CRAB_ROOT": "__CWD__", "CRAB_SYSTEM": "lab"}
    assert config["sbatch"] == []
    assert config["header"] == []


def test_a_preset_crab_root_wins_over_the_engine_default() -> None:
    presets = {"lab": {"scheduler": "slurm", "env": {"CRAB_ROOT": "/mine"}}}
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        config = load_environment_config("lab")

    assert config["env"]["CRAB_ROOT"] == "/mine"


def _empty_run_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    return config_path


def _completion_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "Orchestration complete" in line]


def test_a_wrappers_path_from_the_process_env_is_not_replaced_by_the_shipped_default(
    tmp_path, monkeypatch, shared_presets
):
    # A preset without env (CRAB_ROOT points at an empty folder).
    shared_presets({"local": {"scheduler": "local"}})
    monkeypatch.setattr("crab.cli.orchestrator.CRAB_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "/x")

    with (
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(_empty_run_config(tmp_path)), "local")

    environment = mock_run.call_args.kwargs["environment"]
    assert environment.get("CRAB_PATH_WRAPPERS") in (None, "/x")
    assert not str(environment.get("CRAB_PATH_WRAPPERS", "")).endswith("/wrappers")


def test_a_preset_that_sets_the_wrappers_path_still_puts_it_in_the_job_env(
    tmp_path, monkeypatch, shared_presets
):
    shared_presets({"lab": {"scheduler": "local", "env": {"CRAB_PATH_WRAPPERS": "/mine"}}})
    monkeypatch.setattr("crab.cli.orchestrator.CRAB_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "/x")

    with (
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(_empty_run_config(tmp_path)), "lab")

    assert mock_run.call_args.kwargs["environment"]["CRAB_PATH_WRAPPERS"] == "/mine"


def test_the_completion_log_names_the_local_scheduler(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT}

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset_config),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(str(_empty_run_config(tmp_path)), "lab", as_json=True)

    done = _completion_lines(capsys.readouterr().err)
    assert len(done) == 1
    assert "local scheduler" in done[0]
    assert "SLURM" not in done[0]
    assert "—" not in done[0]


def test_the_completion_log_names_slurm_for_a_slurm_preset(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT}

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset_config),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(str(_empty_run_config(tmp_path)), "lab", as_json=True)

    done = _completion_lines(capsys.readouterr().err)
    assert len(done) == 1
    assert "Slurm" in done[0]
    assert "—" not in done[0]


@pytest.fixture
def marked_lab(tmp_path, monkeypatch, real_preset_dirs):
    """A shared `lab` preset with an unset `--account` mark; returns the overrides dir (not made)."""
    shared = tmp_path / "shared"
    (shared / "presets").mkdir(parents=True)
    (shared / "presets" / "lab.json").write_text(
        json.dumps({"lab": {"scheduler": "slurm", "sbatch": ["--account=<YOUR_ACCOUNT>"]}})
    )
    overrides = tmp_path / "overrides"
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(shared))
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(overrides))
    return overrides


def test_an_unset_mark_refuses_the_preset_with_a_paste_ready_snippet(tmp_path, marked_lab):
    with pytest.raises(ValueError) as exc:
        load_environment_config("lab", tmp_path)

    message = str(exc.value)
    assert "Preset 'lab' needs your own value for --account." in message
    assert f"{marked_lab}/" in message
    snippet = json.loads(message[message.index("{") :])
    assert snippet == {"lab": {"sbatch": ["--account=<YOUR_ACCOUNT>"]}}


def test_an_override_of_the_mark_lets_the_preset_load(tmp_path, marked_lab):
    marked_lab.mkdir()
    (marked_lab / "mine.json").write_text(json.dumps({"lab": {"sbatch": ["--account=REAL"]}}))

    config = load_environment_config("lab", tmp_path)

    assert config["sbatch"] == ["--account=REAL"]


def test_allow_unset_marks_loads_the_preset_with_the_mark_in_place(tmp_path, marked_lab):
    config = load_environment_config("lab", tmp_path, allow_unset_marks=True)

    assert config["sbatch"] == ["--account=<YOUR_ACCOUNT>"]


def _env_preset(env: dict[str, str]) -> dict[str, dict]:
    return {"p": {"scheduler": "slurm", "env": env}}


def test_a_relative_wrappers_entry_in_a_preset_env_is_refused_naming_the_preset() -> None:
    presets = _env_preset({"CRAB_PATH_WRAPPERS": "/abs/a:rel/b"})
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        with pytest.raises(ValueError) as exc:
            load_environment_config("p")

    message = str(exc.value)
    assert "Preset 'p'" in message
    assert "CRAB_PATH_WRAPPERS" in message
    assert "absolute" in message


def test_a_relative_crab_root_in_a_preset_env_is_refused_naming_the_preset() -> None:
    presets = _env_preset({"CRAB_ROOT": "rel/root"})
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        with pytest.raises(ValueError) as exc:
            load_environment_config("p")

    message = str(exc.value)
    assert "Preset 'p'" in message
    assert "CRAB_ROOT" in message
    assert "absolute" in message


@pytest.mark.parametrize("crab_root", ["__CWD__", "/abs/root", "~/crab", "__CWD__/sub"])
def test_a_preset_crab_root_that_is_not_relative_still_loads(crab_root: str) -> None:
    with patch(
        "crab.cli.presets.load_all_presets", return_value=_env_preset({"CRAB_ROOT": crab_root})
    ):
        config = load_environment_config("p")

    assert config["env"]["CRAB_ROOT"] == crab_root


@pytest.mark.parametrize(
    "wrappers", ["$SCRATCH/w:/abs/b", "~/w", "/abs/a:/abs/b", "/abs/a::/abs/b", "__CWD__/w"]
)
def test_a_preset_wrappers_path_that_is_not_relative_still_loads(wrappers: str) -> None:
    presets = _env_preset({"CRAB_PATH_WRAPPERS": wrappers})
    with patch("crab.cli.presets.load_all_presets", return_value=presets):
        config = load_environment_config("p")

    assert config["env"]["CRAB_PATH_WRAPPERS"] == wrappers
