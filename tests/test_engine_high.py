"""
Local-only tests for HIGH-priority engine.py issues.
"""

import os
import subprocess
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


def _minimal_config(tmpdir):
    return {
        "global_options": {"numnodes": 2, "ppn": 8, "datapath": tmpdir},
        "experiments": {},
    }


# ---------------------------------------------------------------------------
# Issue: sbatch stderr not captured
# ---------------------------------------------------------------------------


class TestSbatchStderr(unittest.TestCase):
    def test_stderr_kwarg_passed_to_check_output(self):
        """check_output for sbatch must be called with stderr=subprocess.STDOUT."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _minimal_config(tmpdir)
            with patch("subprocess.check_output", return_value="Submitted batch job 1") as mock_co:
                engine._run_orchestrator(config, {})
            _, kwargs = mock_co.call_args
            self.assertEqual(
                kwargs.get("stderr"),
                subprocess.STDOUT,
                "sbatch check_output must pass stderr=subprocess.STDOUT",
            )


# ---------------------------------------------------------------------------
# Issue: Submitted Slurm job orphaned on KeyboardInterrupt
# ---------------------------------------------------------------------------


class TestJobOrphanedOnInterrupt(unittest.TestCase):
    def test_scancel_called_on_keyboard_interrupt_after_sbatch(self):
        """scancel must be called with the parsed job ID when KeyboardInterrupt fires post-sbatch."""
        engine = _make_engine()
        # Make log.info raise KeyboardInterrupt on the second call
        # (first call = "Engine running in ORCHESTRATOR mode"; second = logged sbatch output)
        # log.info calls in _run_orchestrator:
        #   1: "Engine running in ORCHESTRATOR mode"
        #   2: "Submitting: sbatch ..."
        #   3: the sbatch output ("Submitted batch job 12345") ← interrupt here
        call_count = [0]

        def log_then_interrupt(msg):
            call_count[0] += 1
            if call_count[0] >= 3:
                raise KeyboardInterrupt

        engine.log.info.side_effect = log_then_interrupt

        scancel_calls = []

        def track_run(cmd, **kwargs):
            scancel_calls.append(list(cmd))
            return MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            config = _minimal_config(tmpdir)
            with patch("subprocess.check_output", return_value="Submitted batch job 12345"):
                with patch("subprocess.run", side_effect=track_run):
                    with self.assertRaises(KeyboardInterrupt):
                        engine._run_orchestrator(config, {})

        scancel_cmds = [c for c in scancel_calls if "scancel" in str(c)]
        self.assertTrue(scancel_cmds, "scancel must be called after sbatch + KeyboardInterrupt")
        self.assertIn("12345", str(scancel_cmds[0]))


# ---------------------------------------------------------------------------
# Issue: CRAB_SYSTEM unsanitized in filesystem path — directory traversal
# ---------------------------------------------------------------------------


class TestCrabSystemSanitization(unittest.TestCase):
    def test_traversal_path_stays_inside_datapath(self):
        """CRAB_SYSTEM='../../evil' must not produce directories outside data_path."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _minimal_config(tmpdir)
            with patch("subprocess.check_output", return_value="Submitted batch job 1"):
                engine._run_orchestrator(config, {"CRAB_SYSTEM": "../../evil"})

            for root, dirs, _ in os.walk(tmpdir):
                for d in dirs:
                    path = os.path.join(root, d)
                    self.assertTrue(
                        os.path.abspath(path).startswith(os.path.abspath(tmpdir)),
                        f"Directory escaped data_path: {path}",
                    )

    def test_valid_crab_system_produces_directory(self):
        """A normal CRAB_SYSTEM value must still produce a usable output directory."""
        engine = _make_engine()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _minimal_config(tmpdir)
            with patch("subprocess.check_output", return_value="Submitted batch job 1"):
                engine._run_orchestrator(config, {"CRAB_SYSTEM": "leonardo-dcgp"})

            found = any(
                "leonardo" in p
                for root, dirs, _ in os.walk(tmpdir)
                for p in [os.path.join(root, d) for d in dirs]
            )
            self.assertTrue(found, "Valid CRAB_SYSTEM must produce a directory with that name")


# ---------------------------------------------------------------------------
# Issue: os.path.expandvars applied incrementally — earlier entries pollute later
# ---------------------------------------------------------------------------


class TestExpandvarsIsolation(unittest.TestCase):
    def test_env_var_from_dict_not_expanded_into_sibling_value(self):
        """
        MY_REF='${MY_NEW}' must NOT expand to MY_NEW's value ('hello') when both
        are in the same environment dict. Expansion uses the original os.environ,
        not the incrementally-updated one.
        """
        engine = _make_engine()
        config = {"global_options": {}, "experiments": {}}
        environment = {"MY_NEW": "hello", "MY_REF": "${MY_NEW}"}

        # Ensure MY_NEW is absent from the real env so we have a clean baseline
        env_without_my_new = {
            k: v for k, v in os.environ.items() if k not in ("MY_NEW", "MY_REF", "SLURM_NODELIST")
        }

        captured = {}

        with patch.dict(os.environ, env_without_my_new, clear=True):
            try:
                engine._run_worker(config, environment, "/tmp")
            except Exception:
                pass
            # Check what MY_REF was set to — captured before cleanup
            captured["MY_REF"] = os.environ.get("MY_REF", "__NOT_SET__")

        # With the bug: MY_REF == "hello" (MY_NEW was set before MY_REF's expansion)
        # With the fix: MY_REF == "" or "${MY_NEW}" (MY_NEW absent from original env)
        self.assertNotEqual(
            captured["MY_REF"],
            "hello",
            "MY_REF should not expand ${MY_NEW} from the same environment dict "
            "(that would be incremental pollution)",
        )
