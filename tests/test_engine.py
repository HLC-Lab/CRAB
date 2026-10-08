"""
Local-only tests for engine.py critical issues.
These tests do NOT require a real Slurm environment.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from crab.core.engine import Engine
from crab.core.execution.scheduler.slurm import SlurmScheduler
from crab.core.execution.settings import from_json, to_json
from settings_fixtures import LOCAL_DIRECT, slurm_settings


def _make_engine():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.error = MagicMock()
    return Engine(logger=log)


# ---------------------------------------------------------------------------
# Issue 1: SLURM_NODELIST unset → TypeError from subprocess.call(None in list)
# ---------------------------------------------------------------------------


class TestSlurmNodelistUnset(unittest.TestCase):
    def test_missing_nodelist_raises_runtime_error(self):
        """_run_worker must raise RuntimeError, not TypeError, when SLURM_NODELIST is absent."""
        engine = _make_engine()
        env = os.environ.copy()
        env.pop("SLURM_NODELIST", None)

        config = {"global_options": {}, "experiments": {}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {}, clear=True):
                os.environ.update({k: v for k, v in env.items() if k != "SLURM_NODELIST"})
                with self.assertRaises(RuntimeError) as ctx:
                    engine._run_worker(config, {}, tmpdir)
        self.assertIn("SLURM_NODELIST", str(ctx.exception))

    def test_local_scheduler_uses_localhost_when_nodelist_absent(self):
        """A local scheduler must bypass the SLURM_NODELIST requirement and use a single
        'localhost' node instead of raising or calling scontrol."""
        engine = _make_engine()
        config = {"global_options": {}, "experiments": {}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {}, clear=True):
                # Provenance runs git through subprocess too; this test is only about scontrol.
                with (
                    patch("crab.core.engine.write_provenance"),
                    patch("subprocess.run") as mock_scontrol,
                ):
                    engine._run_worker(config, {}, tmpdir, settings=LOCAL_DIRECT)
                mock_scontrol.assert_not_called()
        engine.log.info.assert_any_call("Allocated 1 node(s)")

    def test_scontrol_nonzero_exit_raises(self):
        """If scontrol exits non-zero the worker must fail."""
        engine = _make_engine()
        config = {"global_options": {}, "experiments": {}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SLURM_NODELIST": "node01"}, clear=False):
                with patch("subprocess.run") as mock_run:
                    mock_run.side_effect = __import__("subprocess").CalledProcessError(
                        1, "scontrol"
                    )
                    with self.assertRaises(Exception):  # noqa: B017 -- any failure aborting the worker is the contract
                        engine._run_worker(config, {}, tmpdir)


# ---------------------------------------------------------------------------
# Issue 3: Unquoted paths in generated sbatch script (shlex.quote)
# ---------------------------------------------------------------------------


class TestSbatchScriptQuoting(unittest.TestCase):
    def test_workdir_with_spaces_is_quoted(self):
        """data_directory with spaces must be shell-quoted in the generated crab_job.sh."""
        engine = _make_engine()
        g_opts = {"numnodes": "2", "ppn": 8, "walltime": "00:30:00"}
        config = {
            "global_options": g_opts,
            "experiments": {"ex1": {"apps": {}}},
        }
        environment = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a subdirectory with a space in its name
            spaced_dir = os.path.join(tmpdir, "my project")
            os.makedirs(spaced_dir, exist_ok=True)

            with patch("subprocess.check_output", return_value="Submitted batch job 99999"):
                with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                    # Patch data_path to write inside tmpdir
                    with patch.object(engine, "_run_orchestrator", wraps=engine._run_orchestrator):
                        # Manually run _run_orchestrator and inspect the generated script
                        g_opts_patched = dict(g_opts)
                        g_opts_patched["datapath"] = spaced_dir
                        config["global_options"] = g_opts_patched
                        try:
                            engine._run_orchestrator(config, environment)
                        except Exception:
                            pass

            # Find the generated script
            scripts = []
            for root, _, files in os.walk(spaced_dir):
                for f in files:
                    if f == "crab_job.sh":
                        scripts.append(os.path.join(root, f))

            self.assertTrue(scripts, "No crab_job.sh was generated")
            with open(scripts[0]) as f:
                content = f.read()

            # The workdir argument must appear quoted (no bare space adjacent to the path)
            # Find the worker command line
            worker_lines = [
                line for line in content.splitlines() if "worker" in line and "--workdir" in line
            ]
            self.assertTrue(worker_lines, "No worker --workdir line found in script")
            worker_line = worker_lines[0]
            # A properly quoted path looks like: --workdir '/path/with space' or --workdir "/path/with space"
            # An unquoted one would be: --workdir /path/with space  (bare space)
            self.assertNotRegex(
                worker_line,
                r"--workdir [^'\"][^ ]+\s",
                "workdir path with spaces is NOT quoted in generated script",
            )

    def test_system_header_injection_blocked(self):
        """system_header lines containing newlines must be rejected."""
        engine = _make_engine()
        g_opts = {
            "numnodes": "2",
            "ppn": 8,
            "system_header": ["module load gcc", "evil\nrm -rf /"],
        }
        config = {"global_options": g_opts, "experiments": {"ex1": {"apps": {}}}}

        with tempfile.TemporaryDirectory() as tmpdir:
            g_opts["datapath"] = tmpdir
            with patch("subprocess.check_output", return_value="Submitted batch job 99999"):
                engine._run_orchestrator(config, {})

            scripts = []
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    if f == "crab_job.sh":
                        scripts.append(os.path.join(root, f))

            self.assertTrue(scripts, "No crab_job.sh generated")
            with open(scripts[0]) as f:
                content = f.read()

        self.assertNotIn("rm -rf /", content, "Newline injection in system_header was not blocked")

    def test_system_sbatch_newline_injection_blocked(self):
        """system_sbatch directives containing newlines must be rejected."""
        engine = _make_engine()
        g_opts = {
            "numnodes": "2",
            "ppn": 8,
            "system_sbatch": ["--partition=gpu", "--account=proj\nevil_line"],
        }
        config = {"global_options": g_opts, "experiments": {"ex1": {"apps": {}}}}

        with tempfile.TemporaryDirectory() as tmpdir:
            g_opts["datapath"] = tmpdir
            with patch("subprocess.check_output", return_value="Submitted batch job 99999"):
                engine._run_orchestrator(config, {})

            scripts = []
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    if f == "crab_job.sh":
                        scripts.append(os.path.join(root, f))

            self.assertTrue(scripts, "No crab_job.sh generated")
            with open(scripts[0]) as f:
                content = f.read()

        self.assertNotIn("evil_line", content, "Newline injection in system_sbatch was not blocked")


# ---------------------------------------------------------------------------
# Issue 4 & 5: int(None) TypeError + --nodes=None in sbatch header
# ---------------------------------------------------------------------------


class TestNumnodesValidation(unittest.TestCase):
    def test_missing_numnodes_raises_value_error(self):
        """Missing numnodes must raise ValueError with a clear message, not TypeError."""
        engine = _make_engine()
        config = {
            "global_options": {},  # numnodes absent
            "experiments": {"ex1": {"apps": {}}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config["global_options"]["datapath"] = tmpdir
            with self.assertRaises((ValueError, TypeError)) as ctx:
                engine._run_orchestrator(config, {})
        # After fix: must be ValueError, not TypeError
        self.assertIsInstance(
            ctx.exception, ValueError, "Missing numnodes should raise ValueError, not TypeError"
        )

    def test_nodes_none_not_in_sbatch_header(self):
        """SlurmScheduler.generate_header must not produce '--nodes=None'."""
        scheduler = SlurmScheduler(MagicMock(), crab_root="/tmp")
        # numnodes absent
        lines = scheduler.generate_header({}, "/tmp/out")
        for line in lines:
            self.assertNotIn("None", line, f"'None' literal found in sbatch header line: {line}")

    def test_nodes_correct_when_numnodes_set(self):
        """When numnodes is set, --nodes=<value> must appear in the header."""
        scheduler = SlurmScheduler(MagicMock(), crab_root="/tmp")
        lines = scheduler.generate_header({"numnodes": 4, "ppn": 8}, "/tmp/out")
        nodes_lines = [line for line in lines if "--nodes=" in line]
        self.assertEqual(len(nodes_lines), 1)
        self.assertIn("--nodes=4", nodes_lines[0])


# ---------------------------------------------------------------------------
# --only: rerun specific experiment keys from a config
# ---------------------------------------------------------------------------


class TestOnlyExperimentFilter(unittest.TestCase):
    def _run_and_read_config(self, config, only, tmpdir):
        config["global_options"]["datapath"] = tmpdir
        engine = _make_engine()
        with patch("subprocess.check_output", return_value="Submitted batch job 1"):
            engine._run_orchestrator(config, {}, only=only)
        for root, _, files in os.walk(tmpdir):
            if "config.json" in files:
                with open(os.path.join(root, "config.json")) as f:
                    return json.load(f)
        self.fail("No config.json generated")

    def test_only_filters_the_experiments_dict(self):
        """Only the requested keys are written to config.json (and so only they run)."""
        config = {
            "global_options": {"numnodes": "2", "ppn": 8},
            "experiments": {"ex1": {"apps": {}}, "ex2": {"apps": {}}, "ex3": {"apps": {}}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            written = self._run_and_read_config(config, ["ex1", "ex3"], tmpdir)
        self.assertEqual(set(written["experiments"].keys()), {"ex1", "ex3"})

    def test_only_with_unknown_key_raises_value_error(self):
        """A typo'd or removed experiment key must fail clearly, not silently no-op."""
        config = {
            "global_options": {"numnodes": "2", "ppn": 8},
            "experiments": {"ex1": {"apps": {}}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config["global_options"]["datapath"] = tmpdir
            engine = _make_engine()
            with self.assertRaises(ValueError) as ctx:
                engine._run_orchestrator(config, {}, only=["ex1", "ghost"])
        self.assertIn("ghost", str(ctx.exception))

    def test_no_only_runs_every_experiment_unchanged(self):
        """Omitting --only (only=None) is a pure no-op: the regression this must not break."""
        config = {
            "global_options": {"numnodes": "2", "ppn": 8},
            "experiments": {"ex1": {"apps": {}}, "ex2": {"apps": {}}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            written = self._run_and_read_config(config, None, tmpdir)
        self.assertEqual(set(written["experiments"].keys()), {"ex1", "ex2"})


# ---------------------------------------------------------------------------
# A local scheduler (preset scheduler "local"): skip sbatch, submit as a detached local subprocess
# (dev/testing-only no-Slurm path)
# ---------------------------------------------------------------------------


class TestLocalScheduler(unittest.TestCase):
    def _config(self, tmpdir):
        return {
            "global_options": {"numnodes": 1, "ppn": 1, "datapath": tmpdir},
            "experiments": {},
        }

    def test_local_scheduler_never_calls_sbatch(self):
        """A local scheduler must never shell out to sbatch."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.check_output") as mock_sbatch:
                    with patch("subprocess.Popen") as mock_popen:
                        mock_popen.return_value = MagicMock(pid=12345)
                        engine._run_orchestrator(self._config(tmpdir), {}, settings=LOCAL_DIRECT)
            mock_sbatch.assert_not_called()
            mock_popen.assert_called_once()

    def test_local_scheduler_returns_pid_as_job_id(self):
        """The returned job_id must be the spawned subprocess's PID (as a string)."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=12345)
                    result = engine._run_orchestrator(
                        self._config(tmpdir), {}, settings=LOCAL_DIRECT
                    )
        self.assertEqual(result["job_id"], "12345")

    def test_run_refuses_a_config_the_preset_cannot_run_before_any_submit(self):
        """Engine.run hands its settings to check_config: the direct launcher runs one process,
        so 2 nodes is refused before anything is spawned."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            config["global_options"]["numnodes"] = 2
            config["experiments"] = {"e1": {"apps": {"0": {"path": "a.py"}}}}
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.check_output") as mock_sbatch:
                    with patch("subprocess.Popen") as mock_popen:
                        with self.assertRaisesRegex(ValueError, "single process"):
                            engine.run(config, {}, settings=LOCAL_DIRECT)
        mock_sbatch.assert_not_called()
        mock_popen.assert_not_called()

    def test_local_scheduler_writes_state_file(self):
        """A state file keyed by pid must be written so gather_status/cancel can find it later."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=54321)
                    engine._run_orchestrator(self._config(tmpdir), {}, settings=LOCAL_DIRECT)

            state_path = os.path.join(tmpdir, ".crab_local_jobs", "54321.json")
            self.assertTrue(os.path.isfile(state_path), "no local job state file written")
            with open(state_path) as f:
                state = json.load(f)
        self.assertEqual(state["pid"], 54321)
        self.assertIn("data_dir", state)

    def test_local_scheduler_redirects_to_slurm_log_filenames(self):
        """stdout/stderr must be redirected to the same filenames the Slurm path uses,
        so gather_logs (engine.py naming contract) needs zero changes for local jobs."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=1)
                    result = engine._run_orchestrator(
                        self._config(tmpdir), {}, settings=LOCAL_DIRECT
                    )
            _, kwargs = mock_popen.call_args
            self.assertEqual(
                os.path.basename(kwargs["stdout"].name),
                "slurm_output.log",
            )
            self.assertEqual(
                os.path.basename(kwargs["stderr"].name),
                "slurm_error.log",
            )
            self.assertTrue(
                kwargs["stdout"].name.startswith(result["data_dir"]),
                "stdout log must live inside the job's own data directory",
            )

    def test_local_scheduler_captures_exit_code_via_bash_trailer(self):
        """The command must be wrapped so a later, separate CLI invocation can learn the exit
        code (a live Popen handle does not survive across CLI invocations)."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=1)
                    engine._run_orchestrator(self._config(tmpdir), {}, settings=LOCAL_DIRECT)
            args, _ = mock_popen.call_args
            popen_cmd = args[0]
        self.assertEqual(popen_cmd[0], "bash")
        self.assertEqual(popen_cmd[1], "-c")
        self.assertIn("local_exit_code", popen_cmd[2])
        self.assertIn("worker --workdir", popen_cmd[2])

    def test_local_scheduler_detaches_the_process(self):
        """The child must be launched in its own session so it survives the parent exiting."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.CRAB_ROOT", tmpdir):
                with patch("subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=1)
                    engine._run_orchestrator(self._config(tmpdir), {}, settings=LOCAL_DIRECT)
            _, kwargs = mock_popen.call_args
        self.assertTrue(kwargs.get("start_new_session"))

    def test_slurm_path_unaffected_when_scheduler_unset(self):
        """Regression: with default settings, submission must still go through sbatch exactly
        as before, never through subprocess.Popen."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "subprocess.check_output", return_value="Submitted batch job 77"
            ) as mock_sbatch:
                with patch("subprocess.Popen") as mock_popen:
                    engine._run_orchestrator(self._config(tmpdir), {})
            mock_sbatch.assert_called_once()
            mock_popen.assert_not_called()

    def test_environment_scheduler_key_is_no_longer_read(self):
        """The old `CRAB_SCHEDULER` environment key chooses nothing: with no settings the job
        goes to Slurm."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "subprocess.check_output", return_value="Submitted batch job 78"
            ) as mock_sbatch:
                with patch("subprocess.Popen") as mock_popen:
                    result = engine._run_orchestrator(
                        self._config(tmpdir), {"CRAB_SCHEDULER": "local"}
                    )
            mock_sbatch.assert_called_once()
            mock_popen.assert_not_called()
        self.assertEqual(result["job_id"], "78")

    def test_orchestrator_writes_execution_json(self):
        """The settings the job was submitted with are saved next to environment.json, in the
        shape `from_json` reads back."""
        settings = slurm_settings(launchers={"srun": {"flags": ["--cpu-bind=socket"]}})
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.check_output", return_value="Submitted batch job 79"):
                result = engine._run_orchestrator(self._config(tmpdir), {}, settings=settings)
            with open(os.path.join(result["data_dir"], "execution.json")) as f:
                written = json.load(f)
        self.assertEqual(written, to_json(settings))
        self.assertEqual(written["launchers"]["srun"]["flags"], ["--cpu-bind=socket"])
        self.assertEqual(from_json(written), settings)

    def test_worker_hands_its_settings_to_every_runner(self):
        """Each ExperimentRunner gets the settings object the worker was started with."""
        engine = _make_engine()
        config = {
            "global_options": {},
            "experiments": {"a": {"apps": {}}, "b": {"apps": {}}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("crab.core.engine.write_provenance"),
                patch("crab.core.engine.time.sleep"),
                patch("crab.core.engine.ExperimentRunner") as mock_runner,
            ):
                engine._run_worker(config, {}, tmpdir, settings=LOCAL_DIRECT)
        self.assertEqual(mock_runner.call_count, 2)
        for call in mock_runner.call_args_list:
            self.assertIs(call.kwargs["settings"], LOCAL_DIRECT)


if __name__ == "__main__":
    unittest.main()


class TestFailedExperimentsFailTheJob(unittest.TestCase):
    def test_worker_runs_every_experiment_then_fails_naming_the_failed_ones(self):
        """An experiment that raises must not stop the others, but the worker must end with an
        error: its exit status is the job's (Slurm state, local_exit_code). Before, it ended
        normally and a crashed job read as COMPLETED."""
        engine = _make_engine()
        missing = {"0": {"path": "/nonexistent/wrapper.py"}}
        config = {
            "global_options": {"numnodes": "1"},
            "experiments": {"a_first": {"apps": missing}, "b_second": {"apps": missing}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("crab.core.engine.write_provenance"):
                with self.assertRaises(RuntimeError) as ctx:
                    engine._run_worker(config, {}, tmpdir, settings=LOCAL_DIRECT)
        self.assertIn("2 of 2 experiments failed: a_first, b_second", str(ctx.exception))
        engine.log.info.assert_any_call("Starting experiment [2/2]: b_second")
