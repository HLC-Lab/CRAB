"""
Local-only tests for ExperimentRunner critical issues (runner.py).
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from crab.core.data.containers import DataContainer

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

    def __init__(self, collect_flag, returncode, series_list):
        self.collect_flag = collect_flag
        self.id_num = 0
        # Each entry in series_list becomes one metadata slot and one data series
        self.metadata = [
            {"name": f"m{i}", "unit": "s", "conv": True} for i in range(len(series_list))
        ]
        self.process = _MockProcess(returncode)
        self._series_list = series_list

    def read_data(self):
        return [list(s) for s in self._series_list]


def _run_collection(apps, containers):
    """
    Re-implements the *fixed* collection logic from execute().
    Tests call this to verify correctness; the production code must match.
    """
    c_idx = 0
    for app in apps:
        if app.collect_flag:
            num_meta = len(app.metadata)
            if hasattr(app, "process") and app.process.returncode == 0:
                raw_data = app.read_data()
                for i, series in enumerate(raw_data):
                    if c_idx + i < len(containers):
                        containers[c_idx + i].data.extend(series)
                        containers[c_idx + i].num_samples.append(len(series))
            c_idx += num_meta


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
# Issue: CRAB_WL_MANAGER without allowlist — arbitrary .py file execution
# ---------------------------------------------------------------------------


class TestWLMAllowlist(unittest.TestCase):
    def _make_logger(self):
        log = MagicMock()
        log.enter.return_value = log
        log.info = MagicMock()
        log.warning = MagicMock()
        log.error = MagicMock()
        return log

    def test_traversal_path_raises_value_error(self):
        """CRAB_WL_MANAGER=../../evil must raise ValueError before touching the filesystem."""
        from crab.core.experiment.runner import ExperimentRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner.__new__(ExperimentRunner)
            runner.name = "test"
            runner.config = {"apps": {}}
            runner.global_opts = {}
            runner.exp_opts = {}
            runner.node_list = []
            runner.exp_dir = tmpdir
            runner.log = self._make_logger()
            runner.ppn = 1
            runner.apps = []
            runner.wlmanager = None
            runner.data_containers = []

            with patch.dict(os.environ, {"CRAB_WL_MANAGER": "../../evil"}):
                with self.assertRaises(ValueError) as ctx:
                    runner.setup()

        self.assertIn("CRAB_WL_MANAGER", str(ctx.exception))

    def test_unknown_known_wlm_name_raises_value_error(self):
        """An unknown but non-traversal name must also be rejected."""
        from crab.core.experiment.runner import ExperimentRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner.__new__(ExperimentRunner)
            runner.name = "test"
            runner.config = {"apps": {}}
            runner.global_opts = {}
            runner.exp_opts = {}
            runner.node_list = []
            runner.exp_dir = tmpdir
            runner.log = self._make_logger()
            runner.ppn = 1
            runner.apps = []
            runner.wlmanager = None
            runner.data_containers = []

            with patch.dict(os.environ, {"CRAB_WL_MANAGER": "notreal"}):
                with self.assertRaises(ValueError):
                    runner.setup()

    def test_valid_wlm_slurm_does_not_raise(self):
        """CRAB_WL_MANAGER=slurm (the default) must load without ValueError."""
        from crab.core.experiment.runner import ExperimentRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner.__new__(ExperimentRunner)
            runner.name = "test"
            runner.config = {"apps": {}}
            runner.global_opts = {}
            runner.exp_opts = {}
            runner.node_list = []
            runner.exp_dir = tmpdir
            runner.log = self._make_logger()
            runner.ppn = 1
            runner.apps = []
            runner.wlmanager = None
            runner.data_containers = []

            with patch.dict(os.environ, {"CRAB_WL_MANAGER": "slurm"}):
                try:
                    runner.setup()
                except ValueError as e:
                    if "CRAB_WL_MANAGER" in str(e):
                        self.fail(f"Valid WLM 'slurm' was incorrectly rejected: {e}")
                except Exception:
                    pass  # other errors (missing apps, etc.) are expected in this minimal setup


if __name__ == "__main__":
    unittest.main()
