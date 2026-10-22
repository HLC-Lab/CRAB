"""
Tests for ExperimentRunner._write_to_registry's per-run failure-count columns
(run failure counts). Calls the method against a minimal stub object rather than a
fully constructed ExperimentRunner, matching test_runner.py's existing
pattern -- _write_to_registry only touches self.exp_dir/global_opts/ppn/apps.
"""

import csv
import datetime
import os
import tempfile
import types
import unittest

from crab.cli import contract
from crab.core.experiment.runner import ExperimentRunner


def _stub(exp_dir):
    return types.SimpleNamespace(
        exp_dir=exp_dir,
        global_opts={"name": "demo_job", "numnodes": 2, "tags": "none"},
        ppn=4,
        started_at=None,
        apps=[],
        allocation_record={},
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
        # explicitly declined migrating these).
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
        # The new row is cut to the old header's width: the file stays rectangular
        # (pandas.read_csv fails on a row wider than the header).
        self.assertNotIn("total_runs", rows[1])
        self.assertNotIn("failed_runs", rows[1])
        self.assertEqual(rows[1]["job_name"], "demo_job")
        with open(self._registry_path(), newline="", encoding="utf-8") as f:
            self.assertEqual({len(r) for r in csv.reader(f)}, {len(old_headers)})

    def test_appending_to_an_old_shape_file_without_a_seed_column_keeps_the_old_header(self):
        # The 11-column header of a file written before the seed column existed.
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
            "total_runs",
            "failed_runs",
        ]
        with open(self._registry_path(), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(old_headers)
            writer.writerow(
                ["old_job", "old_exp", "2026-01-01_00-00-00", "1", "4", "app", "COMPLETED"]
                + ["none", "./old_job/old_exp", "5", "0"]
            )

        stub = _stub(self.exp_dir)
        stub.allocation_record = {"seed": 7}
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=3, failed_runs=1)

        rows = self._read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["total_runs"], "3")
        self.assertEqual(rows[1]["failed_runs"], "1")
        self.assertNotIn("seed", rows[1])
        # Every row has the header's 11 cells: the file stays rectangular.
        with open(self._registry_path(), newline="", encoding="utf-8") as f:
            self.assertEqual({len(r) for r in csv.reader(f)}, {len(old_headers)})

    def test_history_of_an_old_11_column_file_with_a_new_row_has_no_seed_and_right_counts(self):
        old_headers = list(contract._HISTORY_COLUMNS[:-1])
        with open(self._registry_path(), "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(old_headers)
            writer.writerow(["j1", "e1", "", "", "", "", "COMPLETED", "", "", "5", "0"])

        stub = _stub(self.exp_dir)
        stub.allocation_record = {"seed": 7}
        ExperimentRunner._write_to_registry(stub, status="FAILED", total_runs=3, failed_runs=1)

        entries = contract.gather_history(data_root=self.tmp)["experiments"]
        self.assertEqual([e["seed"] for e in entries], ["", ""])
        self.assertEqual([e["total_runs"] for e in entries], ["5", "3"])
        self.assertEqual([e["failed_runs"] for e in entries], ["0", "1"])


class TestRegistrySeedColumn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.system_dir = os.path.join(self.tmp, "leonardo")
        self.exp_dir = os.path.join(self.system_dir, "demo_job_2026-07-08_10-00-00", "01_baseline")
        os.makedirs(self.exp_dir)

    def _read(self):
        with open(os.path.join(self.system_dir, "metadata.csv"), newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_fresh_file_header_ends_with_the_seed_column(self):
        stub = _stub(self.exp_dir)
        stub.allocation_record = {"seed": 123}
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=10, failed_runs=0)

        header, row = self._read()
        self.assertEqual(header[-3:], ["total_runs", "failed_runs", "seed"])
        self.assertEqual(row[-1], "123")

    def test_a_deterministic_allocation_writes_an_empty_seed_cell(self):
        stub = _stub(self.exp_dir)
        stub.allocation_record = {"apps": {}}
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=10, failed_runs=0)

        header, row = self._read()
        self.assertEqual(len(row), len(header))
        self.assertEqual(row[-1], "")

    def test_a_none_seed_writes_an_empty_cell(self):
        stub = _stub(self.exp_dir)
        stub.allocation_record = {"seed": None}
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=1, failed_runs=0)

        _, row = self._read()
        self.assertEqual(row[-1], "")

    def test_history_returns_the_seed_of_a_new_shape_row(self):
        stub = _stub(self.exp_dir)
        stub.allocation_record = {"seed": 123}
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=10, failed_runs=0)

        entry = contract.gather_history(data_root=self.tmp)["experiments"][0]
        self.assertEqual(entry["seed"], "123")

    def test_history_reads_an_old_shape_file_as_an_empty_seed(self):
        with open(os.path.join(self.system_dir, "metadata.csv"), "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(contract._HISTORY_COLUMNS[:-1])
            writer.writerow(["j1", "e1", "", "", "", "", "COMPLETED", "", "", "10", "0"])

        entry = contract.gather_history(data_root=self.tmp)["experiments"][0]
        self.assertEqual(entry["seed"], "")
        self.assertEqual(entry["total_runs"], "10")


class TestRegistryTimestamp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _row_for(self, job_dir_name, **stub_extra):
        # A SbatchMan job dir sits in a tag folder: <tag>/<job>/<experiment>.
        tag_dir = os.path.join(self.tmp, "tag")
        exp_dir = os.path.join(tag_dir, job_dir_name, "run")
        os.makedirs(exp_dir)
        stub = _stub(exp_dir)
        for key, value in stub_extra.items():
            setattr(stub, key, value)
        ExperimentRunner._write_to_registry(stub, status="COMPLETED", total_runs=3, failed_runs=0)
        with open(os.path.join(tag_dir, "metadata.csv"), newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_normal_layout_keeps_the_folder_timestamp(self):
        row = self._row_for(
            "demo_job_2026-07-08_10-00-00-123456",
            started_at=datetime.datetime(2030, 1, 1, 0, 0, 0).timestamp(),
        )
        self.assertEqual(row["timestamp"], "2026-07-08_10-00-00")

    def test_sbatchman_layout_uses_the_experiment_start_time(self):
        started = datetime.datetime(2026, 10, 15, 9, 30, 5)
        row = self._row_for("job_0", started_at=started.timestamp())
        self.assertEqual(row["timestamp"], "2026-10-15_09-30-05")
        self.assertEqual(row["relative_path"], "./job_0/run")

    def test_sbatchman_layout_without_a_start_time_uses_the_write_time(self):
        before = datetime.datetime.now().replace(microsecond=0)
        row = self._row_for("job_0")
        after = datetime.datetime.now()
        self.assertRegex(row["timestamp"], r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
        stamp = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d_%H-%M-%S")
        self.assertTrue(before <= stamp <= after)


if __name__ == "__main__":
    unittest.main()
