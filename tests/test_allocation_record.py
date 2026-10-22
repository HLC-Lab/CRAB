"""ExperimentRunner.setup() writes the experiment's allocation record (mode, seed, each app's
nodes) to `crab_run.json` under `allocations[<experiment>]`, keyed by the same app ids as
`launches`, and logs it."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT

NODES = ["n0", "n1", "n2", "n3"]


def _setup(tmp_path: Path, allocation: dict, logger: MagicMock | None = None) -> dict:
    wrapper = tmp_path / "w.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def run_app(self):\n"
        "        return 'true'\n"
    )
    job_dir = tmp_path / "system" / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "crab_run.json").write_text(json.dumps({"nodes": NODES, "launches": {}}))
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            # Config keys "a" and "b" (not 0 and 1): launches use them, the allocator uses indexes.
            "apps": {"a": {"path": str(wrapper)}, "b": {"path": str(wrapper)}},
            "local_options": {"allocation": allocation},
        },
        global_options={"numnodes": "4"},
        node_list=NODES,
        output_dir=str(job_dir),
        logger=logger or MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    return json.loads((job_dir / "crab_run.json").read_text())


def test_a_random_allocation_without_a_seed_records_the_drawn_seed_and_each_apps_nodes(
    tmp_path: Path,
) -> None:
    data = _setup(tmp_path, {"mode": "random"})

    entry = data["allocations"]["exp"]
    assert entry["mode"] == "random"
    assert isinstance(entry["seed"], int)
    assert sorted(entry["apps"]) == ["a", "b"]
    assert sorted(entry["apps"]["a"] + entry["apps"]["b"]) == NODES
    assert len(entry["apps"]["a"]) == 2
    assert data["nodes"] == NODES


def test_a_linear_allocation_records_no_seed(tmp_path: Path) -> None:
    data = _setup(tmp_path, {"mode": "linear"})

    assert data["allocations"]["exp"] == {
        "mode": "linear",
        "apps": {"a": ["n0", "n1"], "b": ["n2", "n3"]},
    }


def test_setup_logs_the_mode_the_seed_and_each_apps_nodes(tmp_path: Path) -> None:
    logger = MagicMock()
    data = _setup(tmp_path, {"mode": "random", "seed": 123456}, logger)

    assert data["allocations"]["exp"]["seed"] == 123456
    lines = [call.args[0] for call in logger.enter.return_value.info.call_args_list]
    assert "Allocation: random, seed 123456" in lines
    apps = data["allocations"]["exp"]["apps"]
    assert f"App a: {','.join(apps['a'])}" in lines
    assert f"App b: {','.join(apps['b'])}" in lines
