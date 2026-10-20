"""ExperimentRunner.setup gives a chained app (start "sN") its chain head's nodes (ADR-032);
run_job launches each app on `app.node_list`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def test_setup_gives_chained_apps_their_heads_nodes(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "echo.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        "        return 'echo 1'\n"
    )
    apps = {
        "0": {"path": str(wrapper), "collect": False, "partition": "victim"},
        "1": {"path": str(wrapper), "collect": False, "start": "s0"},
        "2": {"path": str(wrapper), "collect": False, "partition": "aggressor"},
        "3": {"path": str(wrapper), "collect": False, "start": "s2"},
    }
    allocation = {"partitions": {"victim": {"share": 75}, "aggressor": {"share": 25}}}
    runner = ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": {"allocation": allocation}},
        global_options={"numnodes": "4"},
        node_list=["n0", "n1", "n2", "n3"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()

    assert [a.node_list for a in runner.apps] == [
        ["n0", "n1", "n2"],
        ["n0", "n1", "n2"],
        ["n3"],
        ["n3"],
    ]
