"""The per-app loading in ExperimentRunner.setup(): the GPU partition check and the injection of
extra config keys.

A wrapper whose receipt targets a GPU fails setup when the experiment's partition (the
`sbatch_directives` of the global options, a dict or a list of `--partition` flags) has "cpu" in
its name; any other partition, no directive, or a CPU or missing receipt passes. Every key of an
app's config entry that is not a CRAB keyword becomes an attribute of the app instance; the
keywords are consumed by the constructor, the timing metadata or placement, and never land as
attributes under their own name through that loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _wrapper(tmp_path: Path, receipt: dict[str, Any] | None) -> Path:
    """A wrapper that builds its own command (so setup looks for no binary) and returns `receipt`
    from get_receipt, as a receipt file would."""
    path = tmp_path / "gpu_app.py"
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def get_receipt(self):\n"
        f"        return {receipt!r}\n\n"
        "    def run_app(self):\n"
        "        return 'true'\n\n"
        "    def read_data(self):\n"
        "        return []\n"
    )
    return path


def _runner(
    tmp_path: Path,
    wrapper: Path,
    sbatch_directives: Any = None,
    app: dict[str, Any] | None = None,
) -> ExperimentRunner:
    global_options: dict[str, Any] = {"numnodes": "1"}
    if sbatch_directives is not None:
        global_options["sbatch_directives"] = sbatch_directives
    app_config: dict[str, Any] = {"path": str(wrapper), "collect": True}
    app_config.update(app or {})
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": {"0": app_config}, "local_options": {"minruns": "1", "maxruns": "1"}},
        global_options=global_options,
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )


GPU_RECEIPT = {"name": "blink", "target_arch": "gpu"}


def test_a_gpu_app_on_a_cpu_partition_fails_setup(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, GPU_RECEIPT), {"partition": "boost_cpu"})

    with pytest.raises(RuntimeError, match="Architecture Mismatch") as exc:
        runner.setup()
    assert "blink is built for GPU but partition is boost_cpu" in str(exc.value)


def test_the_error_names_the_app_when_the_receipt_has_no_name(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path, {"target_arch": "gpu"})
    runner = _runner(tmp_path, wrapper, {"partition": "boost_cpu"})

    with pytest.raises(RuntimeError, match="Architecture Mismatch") as exc:
        runner.setup()
    assert "app is built for GPU but partition is boost_cpu" in str(exc.value)


@pytest.mark.parametrize(
    "directives",
    [
        ["--partition=lrd_cpu_part", "--time=01:00:00"],
        ["--time=01:00:00", "--partition lrd_cpu_part"],
    ],
)
def test_a_cpu_partition_in_a_directive_list_fails_setup(
    tmp_path: Path, directives: list[str]
) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, GPU_RECEIPT), directives)

    with pytest.raises(RuntimeError, match="Architecture Mismatch") as exc:
        runner.setup()
    assert "built for GPU but partition is lrd_cpu_part" in str(exc.value)


def test_the_first_partition_directive_of_a_list_decides(tmp_path: Path) -> None:
    directives = ["--partition=boost_usr_prod", "--partition=lrd_cpu_part"]
    runner = _runner(tmp_path, _wrapper(tmp_path, GPU_RECEIPT), directives)
    runner.setup()

    assert len(runner.apps) == 1


@pytest.mark.parametrize(
    "directives",
    [
        {"partition": "boost_usr_prod"},
        None,
        {},
        ["--time=01:00:00", "--account=abc"],
        ["--partition=boost_usr_prod"],
        "--partition=lrd_cpu_part",
    ],
    ids=[
        "gpu-partition",
        "no-directives",
        "empty-dict",
        "list-without-partition",
        "gpu-list",
        "string",
    ],
)
def test_a_gpu_app_passes_when_the_partition_is_not_a_cpu_one(
    tmp_path: Path, directives: Any
) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, GPU_RECEIPT), directives)
    runner.setup()

    assert len(runner.apps) == 1


@pytest.mark.parametrize("receipt", [{"name": "stream", "target_arch": "cpu"}, {"name": "x"}, None])
def test_the_check_is_for_gpu_apps_only(tmp_path: Path, receipt: dict[str, Any] | None) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, receipt), {"partition": "boost_cpu"})
    runner.setup()

    assert len(runner.apps) == 1


def test_the_check_reads_the_global_options_not_the_local_ones(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, GPU_RECEIPT))
    runner.config["local_options"]["sbatch_directives"] = {"partition": "boost_cpu"}
    runner.setup()

    assert len(runner.apps) == 1


def test_extra_config_keys_become_attributes_of_the_app(tmp_path: Path) -> None:
    app = {"args": "-n 4", "my_custom_key": 42, "another": "x"}
    runner = _runner(tmp_path, _wrapper(tmp_path, None), app=app)
    runner.setup()

    (instance,) = runner.apps
    assert instance.my_custom_key == 42
    assert instance.another == "x"


def test_the_crab_keywords_are_not_injected_as_attributes(tmp_path: Path) -> None:
    app = {
        "args": "-n 4",
        "start": "5",
        "end": "30",
        "partition": "part_a",
        "ppn": 2,
        "my_custom_key": 42,
    }
    wrapper = _wrapper(tmp_path, None)
    runner = _runner(tmp_path, wrapper, app=app)
    runner.setup()

    (instance,) = runner.apps
    assert instance.my_custom_key == 42
    # The keywords land under the names the engine gives them ...
    assert instance.args == "-n 4"
    assert instance.collect_flag is True
    assert instance.start_string == "5"
    assert instance.config_end == "30"
    assert instance.partition_id == "part_a"
    assert instance.ppn == 2
    # ... and not under their own.
    for keyword in ("path", "start", "end", "partition"):
        assert not hasattr(instance, keyword), keyword
    assert not hasattr(instance, "collect")
