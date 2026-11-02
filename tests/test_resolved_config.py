"""`config.resolved.json` and the refusal to replay it elsewhere (ADR-043).

Before: a config found in a run folder carried no sign of the cluster it ran on, so replaying it on another
cluster, or after the preset's topology changed, silently gave another placement.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import crab
from crab.core.config_checks import check_config
from crab.core.engine import Engine
from crab.core.execution.bind_resolve import RESOLVER_VERSION
from crab.core.execution.resolved import (
    RESOLVED_KEY,
    check_resolved_for,
    has_bind,
    resolved_header,
)
from crab.core.execution.settings import ExecutionSettings
from crab.core.execution.topology import topology_sha256
from settings_fixtures import slurm_settings
from topology_fixtures import SHAPES

ENV = {"CRAB_SYSTEM": "lab"}


def settings_with(shape: str = "four-gpus-four-nics") -> ExecutionSettings:
    return dataclasses.replace(slurm_settings(), topology=SHAPES[shape]())


def config_with(apps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "global_options": {"numnodes": "1", "ppn": 2},
        "experiments": {"e1": {"apps": apps}},
        **extra,
    }


def marker(**changes: str) -> dict[str, str]:
    header = resolved_header(ENV, settings_with())
    assert header is not None
    return {**header, **changes}


def test_has_bind_only_for_a_bind_that_asks_for_something() -> None:
    assert has_bind(config_with({"0": {"bind": {"cores": "nearest"}}}))
    assert not has_bind(config_with({"0": {"bind": {}}}))
    assert not has_bind(config_with({"0": {"path": "x"}}))
    assert not has_bind({"experiments": {"e1": "x"}})
    assert not has_bind({})


def test_the_header_names_the_preset_topology_resolver_and_crab_version() -> None:
    header = resolved_header(ENV, settings_with())
    assert header == {
        "preset": "lab",
        "topology_sha256": topology_sha256(SHAPES["four-gpus-four-nics"]()),
        "resolver_version": RESOLVER_VERSION,
        "crab_version": crab.__version__,
    }
    assert resolved_header(ENV, slurm_settings()) is None


def test_a_config_without_the_header_or_with_a_matching_one_passes() -> None:
    check_resolved_for(config_with({}), ENV, settings_with())
    check_resolved_for(config_with({}, resolved_for=marker()), ENV, settings_with())


def test_another_crab_version_alone_does_not_refuse() -> None:
    check_resolved_for(
        config_with({}, resolved_for=marker(crab_version="0.0.1")), ENV, settings_with()
    )


def test_another_preset_is_refused_naming_both() -> None:
    with pytest.raises(ValueError) as error:
        check_resolved_for(
            config_with({}, resolved_for=marker()), {"CRAB_SYSTEM": "other"}, settings_with()
        )
    assert str(error.value).startswith(
        "this config was resolved for another cluster setup (preset 'lab' became 'other'), so it cannot run here"
    )
    assert "Run the config.json of the same run folder instead" in str(error.value)


def test_a_changed_topology_is_refused_with_both_hashes_shortened() -> None:
    new = settings_with("eight-gpus-two-nics")
    with pytest.raises(ValueError) as error:
        check_resolved_for(config_with({}, resolved_for=marker()), ENV, new)
    old_hash = topology_sha256(SHAPES["four-gpus-four-nics"]())[:12]
    new_hash = topology_sha256(SHAPES["eight-gpus-two-nics"]())[:12]
    assert f"(topology '{old_hash}' became '{new_hash}')" in str(error.value)


def test_a_preset_that_lost_its_topology_and_another_resolver_are_refused() -> None:
    with pytest.raises(ValueError, match=r"topology '[0-9a-f]{12}' became 'none'"):
        check_resolved_for(config_with({}, resolved_for=marker()), ENV, slurm_settings())
    with pytest.raises(ValueError, match="resolver '0' became '1'"):
        check_resolved_for(
            config_with({}, resolved_for=marker(resolver_version="0")), ENV, settings_with()
        )


def test_every_difference_is_listed_together() -> None:
    with pytest.raises(ValueError) as error:
        check_resolved_for(
            config_with({}, resolved_for=marker(preset="x", resolver_version="0")),
            ENV,
            settings_with(),
        )
    assert "preset 'x' became 'lab'; resolver '0' became '1'" in str(error.value)


@pytest.mark.parametrize(
    "bad",
    ["lab", [], {"preset": "lab"}, {"preset": 1, "topology_sha256": "a", "resolver_version": "1"}],
)
def test_a_malformed_header_is_refused(bad: Any) -> None:
    with pytest.raises(
        ValueError, match="resolved_for: must be an object with preset, topology_sha256"
    ):
        check_resolved_for(config_with({}, resolved_for=bad), ENV, settings_with())


def test_the_config_checks_ignore_the_header_key() -> None:
    config = config_with({"0": {"path": "x.py"}}, resolved_for=marker())
    assert isinstance(check_config(config, settings_with()), list)


# --- the engine writes the files ----------------------------------------------------------------


def submit(config: dict[str, Any], datapath: Path, settings: ExecutionSettings) -> Path:
    config["global_options"]["datapath"] = str(datapath)
    engine = Engine(logger=MagicMock())
    with patch("subprocess.check_output", return_value="Submitted batch job 1"):
        engine._run_orchestrator(config, dict(ENV), settings=settings)
    (folder,) = (datapath / "lab").iterdir()
    return folder


def read(folder: Path, name: str) -> dict[str, Any]:
    return json.loads((folder / name).read_text())


def test_a_bind_config_gets_a_resolved_copy_and_an_agnostic_config_json(tmp_path: Path) -> None:
    folder = submit(
        config_with({"0": {"path": "x.py", "bind": {"cores": "nearest"}}}),
        tmp_path,
        settings_with(),
    )
    assert RESOLVED_KEY not in read(folder, "config.json")
    resolved = read(folder, "config.resolved.json")
    assert resolved[RESOLVED_KEY] == resolved_header(ENV, settings_with())
    assert {k: v for k, v in resolved.items() if k != RESOLVED_KEY} == read(folder, "config.json")


def test_without_bind_or_without_a_topology_there_is_no_resolved_copy(tmp_path: Path) -> None:
    plain = submit(config_with({"0": {"path": "x.py"}}), tmp_path / "a", settings_with())
    assert not (plain / "config.resolved.json").exists()
    no_topology = submit(
        config_with({"0": {"path": "x.py", "bind": {"nic": "default", "gpu": "all"}}}),
        tmp_path / "b",
        slurm_settings(),
    )
    assert not (no_topology / "config.resolved.json").exists()


def test_replaying_a_resolved_config_keeps_the_new_config_json_agnostic(tmp_path: Path) -> None:
    first = submit(
        config_with({"0": {"path": "x.py", "bind": {"cores": "nearest"}}}),
        tmp_path / "a",
        settings_with(),
    )
    replay = read(first, "config.resolved.json")
    second = submit(replay, tmp_path / "b", settings_with())
    assert RESOLVED_KEY not in read(second, "config.json")
    assert read(second, "config.resolved.json")[RESOLVED_KEY] == replay[RESOLVED_KEY]


def test_engine_run_refuses_a_replay_before_doing_anything(tmp_path: Path) -> None:
    config = config_with(
        {"0": {"path": "x.py", "bind": {"cores": "nearest"}}}, resolved_for=marker()
    )
    config["global_options"]["datapath"] = str(tmp_path)
    engine = Engine(logger=MagicMock())
    with patch("subprocess.check_output") as submit_job:
        with pytest.raises(ValueError, match="resolved for another cluster setup"):
            engine.run(config, dict(ENV), settings=settings_with("eight-gpus-two-nics"))
    submit_job.assert_not_called()
    assert list(tmp_path.iterdir()) == []
