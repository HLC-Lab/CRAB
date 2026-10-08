"""
Local-only tests for ExperimentRunner critical issues (runner.py).
"""

import unittest
from unittest.mock import MagicMock

import pytest

from crab.core.data.containers import DataContainer
from crab.core.data.parse import collect_run
from settings_fixtures import LOCAL_DIRECT, slurm_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockProcess:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class _MockApp:
    """Minimal app stub for collection-logic testing."""

    _next_id = 0

    def __init__(self, collect_flag, returncode, series_list):
        self.collect_flag = collect_flag
        # Apps in one experiment have distinct ids (the runner numbers them in order).
        self.id_num = _MockApp._next_id
        _MockApp._next_id += 1
        # Each entry in series_list becomes one metadata slot and one data series
        self.metadata = [
            {"name": f"m{i}", "unit": "s", "conv": True} for i in range(len(series_list))
        ]
        self.process = _MockProcess(returncode)
        self._series_list = series_list

    def read_data(self):
        return [list(s) for s in self._series_list]


def _run_collection(apps, containers):
    """The runner's real per-run collection step (core/data/parse.py)."""
    return collect_run(apps, containers, MagicMock(), run_id=1)


# ---------------------------------------------------------------------------
# Issue: c_idx misalignment when a collect-app fails
# ---------------------------------------------------------------------------


class TestCIdxAlignment(unittest.TestCase):
    def _make_containers(self, apps):
        containers = []
        for app in apps:
            if app.collect_flag:
                for meta in app.metadata:
                    containers.append(DataContainer(app.id_num, True, meta["name"], meta["unit"]))
        return containers

    def test_failed_app_does_not_shift_subsequent_containers(self):
        """If app[0] fails, app[1]'s data must land in app[1]'s container, not app[0]'s."""
        app0 = _MockApp(collect_flag=True, returncode=1, series_list=[[]])  # fails
        app1 = _MockApp(collect_flag=True, returncode=0, series_list=[[42.0]])  # succeeds

        containers = self._make_containers([app0, app1])
        _run_collection([app0, app1], containers)

        assert containers[0].data == [], "container[0] (failed app) must stay empty"
        assert containers[1].data == [42.0], (
            "container[1] (second app, succeeded) must have [42.0], "
            "not be shifted into container[0]"
        )

    def test_two_failing_apps_leave_all_containers_empty(self):
        """All containers remain empty when all collect apps fail."""
        app0 = _MockApp(collect_flag=True, returncode=1, series_list=[[]])
        app1 = _MockApp(collect_flag=True, returncode=1, series_list=[[]])

        containers = self._make_containers([app0, app1])
        _run_collection([app0, app1], containers)

        assert containers[0].data == []
        assert containers[1].data == []

    def test_successful_apps_still_fill_correctly(self):
        """When all apps succeed, data must land in the correct containers."""
        app0 = _MockApp(collect_flag=True, returncode=0, series_list=[[1.0, 2.0]])
        app1 = _MockApp(collect_flag=True, returncode=0, series_list=[[99.0]])

        containers = self._make_containers([app0, app1])
        _run_collection([app0, app1], containers)

        assert containers[0].data == [1.0, 2.0]
        assert containers[1].data == [99.0]

    def test_non_collect_app_does_not_consume_container_slot(self):
        """An app with collect_flag=False must not consume a container index."""
        app0 = _MockApp(collect_flag=False, returncode=0, series_list=[])  # not collected
        app1 = _MockApp(collect_flag=True, returncode=0, series_list=[[7.0]])

        containers = self._make_containers([app0, app1])
        _run_collection([app0, app1], containers)

        # Only app1 has a container (index 0)
        assert len(containers) == 1
        assert containers[0].data == [7.0]

    def test_multi_metric_app_failure_skips_all_its_slots(self):
        """A failing app with N metrics must skip exactly N container slots."""
        app0 = _MockApp(collect_flag=True, returncode=1, series_list=[[], []])  # 2 metrics, fails
        app1 = _MockApp(collect_flag=True, returncode=0, series_list=[[3.14]])  # 1 metric

        containers = self._make_containers([app0, app1])
        _run_collection([app0, app1], containers)

        assert containers[0].data == [], "slot 0 (app0 metric0)"
        assert containers[1].data == [], "slot 1 (app0 metric1)"
        assert containers[2].data == [3.14], "slot 2 (app1 metric0)"


# ---------------------------------------------------------------------------
# The launcher of each app is resolved at setup, from the settings
# ---------------------------------------------------------------------------

_WRAPPER = (
    "from crab.wrappers.base import base\n\n"
    "class app(base):\n"
    "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
    "    def run_app(self):\n"
    "        return 'echo 1'\n"
)
_MPIRUN_WRAPPER = _WRAPPER + "\n    def get_launcher_override(self):\n        return 'mpirun'\n"
_BAD_OVERRIDE_WRAPPER = (
    _WRAPPER + "\n    def get_launcher_override(self):\n        return '/opt/x/mpirun'\n"
)


def _runner(tmp_path, wrappers, settings, local_options=None):
    """A runner whose app i is the wrapper source wrappers[i]."""
    from crab.core.experiment.runner import ExperimentRunner

    apps = {}
    for i, source in enumerate(wrappers):
        path = tmp_path / f"w{i}.py"
        path.write_text(source)
        apps[str(i)] = {"path": str(path), "collect": False}
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": local_options or {}},
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
        settings=settings,
    )


def test_setup_gives_every_app_the_presets_launcher(tmp_path):
    runner = _runner(tmp_path, [_WRAPPER, _WRAPPER], LOCAL_DIRECT)
    runner.setup()
    assert [spec.kind for spec in runner.launchers] == ["direct", "direct"]


def test_setup_without_settings_falls_back_to_slurm_srun(tmp_path):
    runner = _runner(tmp_path, [_WRAPPER], None)
    runner.setup()
    assert [spec.kind for spec in runner.launchers] == ["srun"]


def test_setup_uses_mpirun_for_an_app_whose_receipt_says_mpirun(tmp_path):
    runner = _runner(tmp_path, [_WRAPPER, _MPIRUN_WRAPPER], slurm_settings())
    runner.setup()
    assert [spec.kind for spec in runner.launchers] == ["srun", "mpirun"]


def test_setup_refuses_a_receipt_override_that_is_not_a_kind(tmp_path):
    runner = _runner(tmp_path, [_WRAPPER, _BAD_OVERRIDE_WRAPPER], slurm_settings())
    with pytest.raises(ValueError, match="launcher_override") as info:
        runner.setup()
    # The message names the app (id and wrapper path) whose receipt is bad.
    assert f"app 1 ({tmp_path / 'w1.py'}): " in str(info.value)
    assert runner.launchers == []


def test_setup_applies_the_launcher_flags_option_to_srun(tmp_path):
    runner = _runner(
        tmp_path,
        [_WRAPPER],
        slurm_settings(launchers={"srun": {"flags": ["--from-preset"]}}),
        local_options={"launcher_flags": ["--cpu-bind=cores"]},
    )
    runner.setup()
    assert runner.launchers[0].kind == "srun"
    assert runner.launchers[0].flags == ("--cpu-bind=cores",)


if __name__ == "__main__":
    unittest.main()
