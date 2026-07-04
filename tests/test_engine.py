"""
Local-only tests for engine.py critical issues.
These tests do NOT require a real Slurm environment.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from crab.core.engine import Engine


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

    def test_scontrol_nonzero_exit_raises(self):
        """If scontrol exits non-zero the worker must raise, not silently leave an empty file."""
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
# Issue 2: worker_nodelist.txt written to CWD, not output_dir
# ---------------------------------------------------------------------------


class TestNodelistFileLocation(unittest.TestCase):
    def test_nodelist_written_inside_output_dir(self):
        """The temporary nodelist file must be created inside output_dir, not CWD."""
        engine = _make_engine()
        config = {"global_options": {}, "experiments": {}}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SLURM_NODELIST": "node01"}, clear=False):
                written_paths = []

                original_open = open

                def tracking_open(path, *args, **kwargs):
                    written_paths.append(str(path))
                    return original_open(path, *args, **kwargs)

                with patch("builtins.open", side_effect=tracking_open):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        # scontrol writes nothing → pandas.read_csv will fail, but
                        # we only care about the path the open() call uses
                        try:
                            engine._run_worker(config, {}, tmpdir)
                        except Exception:
                            pass

                # The nodelist file open should be inside tmpdir, not in CWD
                nodelist_opens = [p for p in written_paths if "nodelist" in p.lower()]
                if nodelist_opens:
                    for p in nodelist_opens:
                        self.assertTrue(
                            os.path.abspath(p).startswith(os.path.abspath(tmpdir)),
                            f"nodelist file opened outside output_dir: {p}",
                        )


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
        """_generate_sbatch_header must not produce '--nodes=None'."""
        engine = _make_engine()
        # numnodes absent
        lines = engine._generate_sbatch_header({}, "/tmp/out")
        for line in lines:
            self.assertNotIn("None", line, f"'None' literal found in sbatch header line: {line}")

    def test_nodes_correct_when_numnodes_set(self):
        """When numnodes is set, --nodes=<value> must appear in the header."""
        engine = _make_engine()
        lines = engine._generate_sbatch_header({"numnodes": 4, "ppn": 8}, "/tmp/out")
        nodes_lines = [line for line in lines if "--nodes=" in line]
        self.assertEqual(len(nodes_lines), 1)
        self.assertIn("--nodes=4", nodes_lines[0])


if __name__ == "__main__":
    unittest.main()
