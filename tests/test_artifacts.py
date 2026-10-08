"""Wrappers can name extra files (plots, logs, output decks) to keep: `get_extra_artifacts()`
returns paths or glob patterns relative to the app's run directory, and they are copied to
`<exp>/artifacts/run_<n>/app_<id>/`, so they survive `retain_files: false`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.artifacts import copy_artifacts
from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


class _App:
    def __init__(self, run_dir: Path, patterns: list[str]) -> None:
        self.id_num = 1
        self.run_dir = str(run_dir)
        self._patterns = patterns

    def get_extra_artifacts(self) -> list[str]:
        return self._patterns


def _run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "exp" / "run_2" / "app_1"
    (d / "plots").mkdir(parents=True)
    (d / "plots" / "a.png").write_text("png")
    (d / "result.yaml").write_text("yaml")
    (d / "noise.tmp").write_text("x")
    return d


def test_listed_files_and_globs_are_copied(tmp_path: Path) -> None:
    app = _App(_run_dir(tmp_path), ["result.yaml", "plots/*.png"])
    copy_artifacts([app], str(tmp_path / "exp"), 2, MagicMock())
    dest = tmp_path / "exp" / "artifacts" / "run_2" / "app_1"
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == [
        "plots/a.png",
        "result.yaml",
    ]


def test_missing_or_escaping_paths_only_warn(tmp_path: Path) -> None:
    (tmp_path / "secret").write_text("no")
    app = _App(_run_dir(tmp_path), ["absent.txt", "../../../secret", "/etc/hostname"])
    log = MagicMock()
    copy_artifacts([app], str(tmp_path / "exp"), 2, log)
    assert log.warning.call_count == 3
    assert not (tmp_path / "exp" / "artifacts").exists() or not any(
        (tmp_path / "exp" / "artifacts").rglob("secret")
    )


def test_artifacts_survive_retain_files_false(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "w.py"
    wrapper.write_text(
        "import os\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        "        return \"sh -c 'echo 3 > out.txt; echo log > run.log'\"\n\n"
        "    def read_data(self):\n"
        "        with open(os.path.join(self.run_dir, 'out.txt')) as f:\n"
        "            return [[float(f.read())]]\n\n"
        "    def get_extra_artifacts(self):\n"
        "        return ['run.log']\n"
    )
    runner = ExperimentRunner(
        exp_name="e",
        config={
            "apps": {"0": {"path": str(wrapper), "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1", "retain_files": False},
        },
        global_options={"numnodes": "1"},
        node_list=["n1"],
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(tmp_path / "out"))
    exp = Path(runner.exp_dir)
    assert not (exp / "run_1").exists()
    assert (exp / "artifacts" / "run_1" / "app_0" / "run.log").read_text().strip() == "log"
