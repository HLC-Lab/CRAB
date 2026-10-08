"""Each app's process runs inside its own run directory (`<exp>/run_<n>/app_<id>/`), so files
an app writes relative to its working directory land there and never collide with a co-running
app's. Before, Popen had no `cwd` (process/manager.py) and every app inherited the worker's."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crab.core.execution.settings import LauncherSpec
from crab.core.process.manager import run_job
from settings_fixtures import LOCAL_DIRECT

DIRECT = LauncherSpec("direct", (), "", (), ())


class _Job:
    def __init__(self, run_dir: Path, command: str) -> None:
        self.id_num = 0
        self.node_list = ["localhost"]
        self.run_dir = str(run_dir)
        self._command = command

    def run_app(self) -> str:
        return self._command

    def set_process(self, process) -> None:
        self.process = process


def test_the_app_process_starts_in_its_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1" / "app_0"
    run_dir.mkdir(parents=True)
    job = _Job(run_dir, "pwd > where.txt")

    run_job(job, DIRECT, 1, MagicMock())
    job.process.wait(timeout=10)

    assert (run_dir / "where.txt").read_text().strip() == str(run_dir)


def _writer(path: Path, value: int) -> None:
    path.write_text(
        "import os\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        return \"sh -c 'echo {value} > out.txt; sleep 0.3'\"\n\n"
        "    def read_data(self):\n"
        "        with open(os.path.join(self.run_dir, 'out.txt')) as f:\n"
        "            return [[float(f.read())]]\n"
    )


def test_co_running_apps_writing_the_same_file_do_not_collide(tmp_path: Path) -> None:
    from crab.core.experiment.runner import ExperimentRunner

    _writer(tmp_path / "w1.py", 1)
    _writer(tmp_path / "w2.py", 2)
    runner = ExperimentRunner(
        exp_name="pair",
        config={
            "apps": {
                "0": {"path": str(tmp_path / "w1.py"), "collect": True},
                "1": {"path": str(tmp_path / "w2.py"), "collect": True},
            },
            "local_options": {"minruns": "2", "maxruns": "2"},
        },
        global_options={"numnodes": "2"},
        node_list=["n1", "n2"],  # the local launcher ignores node names
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(tmp_path / "out"))

    assert [c.data for c in runner.data_containers] == [[1.0, 1.0], [2.0, 2.0]]
    exp_dir = Path(runner.exp_dir)
    assert (exp_dir / "run_1" / "app_0" / "out.txt").read_text().strip() == "1"
    assert (exp_dir / "run_1" / "app_1" / "out.txt").read_text().strip() == "2"
