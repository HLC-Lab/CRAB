"""
Tests for ExperimentRunner._write_to_registry's per-run failure-count columns
(plan 081). Calls the method against a minimal stub object rather than a
fully constructed ExperimentRunner, matching test_runner.py's existing
pattern -- _write_to_registry only touches self.exp_dir/global_opts/ppn/apps.
"""

import csv
import os
import tempfile
import types
import unittest

from crab.core.experiment.runner import ExperimentRunner


def _stub(exp_dir):
    return types.SimpleNamespace(
        exp_dir=exp_dir,
        global_opts={"name": "demo_job", "numnodes": 2, "tags": "none"},
        ppn=4,
        apps=[],
    )


class TestWriteToRegistryRunCounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.system_dir = os.path.join(self.tmp, "leonardo")
        self.job_dir = os.path.join(self.system_dir, "demo_job_2026-07-08_10-00-00")
        self.exp_dir = os.path.join(self.job_dir, "01_baseline")
        os.makedirs(self.exp_dir)

    def _registry_path(self):
        return os.path.join(self.system_dir, "metadata.csv")

    def _read_rows(self):
        with open(self._registry_path(), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_fresh_file_gets_the_new_columns(self):
        stub = _stub(self.exp_dir)
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=10, failed_runs=0)

        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "COMPLETED")
        self.assertEqual(rows[0]["total_runs"], "10")
        self.assertEqual(rows[0]["failed_runs"], "0")

    def test_second_row_appends_correctly(self):
        stub = _stub(self.exp_dir)
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=10, failed_runs=0)
        ExperimentRunner._write_to_registry(stub, status="FAILED", total_runs=5, failed_runs=2)

        rows = self._read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["status"], "FAILED")
        self.assertEqual(rows[1]["total_runs"], "5")
        self.assertEqual(rows[1]["failed_runs"], "2")

    def test_appending_to_an_existing_old_shape_file_does_not_crash(self):
        # A pre-existing metadata.csv with the OLD 9-column header (owner
        # explicitly declined migrating these -- see plan 081's Design).
        old_headers = [
            "job_name",
            "experiment_name",
            "timestamp",
            "numnodes",
            "ppn",
            "apps_list",
            "status",
            "tags",
            "relative_path",
        ]
        with open(self._registry_path(), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(old_headers)
            writer.writerow(
                [
                    "old_job",
                    "old_exp",
                    "2026-01-01_00-00-00",
                    "1",
                    "4",
                    "app",
                    "COMPLETED",
                    "none",
                    "./old_job/old_exp",
                ]
            )

        stub = _stub(self.exp_dir)
        # Must not raise -- appending to an old-shape file is accepted,
        # documented behavior, not an error.
        ExperimentRunner._write_to_registry(stub, status="FAILED", total_runs=3, failed_runs=1)

        rows = self._read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["job_name"], "old_job")
        # The header never named these columns, so DictReader can't map the
        # new row's extra values to "total_runs"/"failed_runs" by name.
        self.assertNotIn("total_runs", rows[1])
        self.assertNotIn("failed_runs", rows[1])


if __name__ == "__main__":
    unittest.main()
