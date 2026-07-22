"""Local-only tests for HIGH-priority orchestrator, logger, and memory issues."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ── Orchestrator ─────────────────────────────────────────────────────────────


class TestOrchestratorKeyboardInterrupt(unittest.TestCase):
    def test_keyboard_interrupt_not_leaked_in_orchestrator(self):
        """execute_orchestrator must catch KeyboardInterrupt (currently it leaks it)."""
        from crab.cli.orchestrator import execute_orchestrator

        with patch("crab.cli.orchestrator.load_environment_config", side_effect=KeyboardInterrupt):
            with patch("sys.exit") as mock_exit:
                leaked = False
                try:
                    execute_orchestrator("fake.json", "local")
                except KeyboardInterrupt:
                    leaked = True
                if leaked:
                    self.fail(
                        "KeyboardInterrupt leaked out of execute_orchestrator — "
                        "should be caught and call sys.exit()"
                    )
                mock_exit.assert_called()

    def test_keyboard_interrupt_not_leaked_in_worker(self):
        """execute_worker must catch KeyboardInterrupt (currently it leaks it)."""
        import json as _json

        from crab.cli.orchestrator import execute_worker

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "config.json"), "w") as f:
                _json.dump({}, f)
            with open(os.path.join(tmpdir, "environment.json"), "w") as f:
                _json.dump({}, f)
            with patch("crab.core.engine.Engine.run", side_effect=KeyboardInterrupt):
                with patch("sys.exit") as mock_exit:
                    leaked = False
                    try:
                        execute_worker(tmpdir)
                    except KeyboardInterrupt:
                        leaked = True
                    if leaked:
                        self.fail(
                            "KeyboardInterrupt leaked out of execute_worker — "
                            "should be caught and call sys.exit()"
                        )
                    mock_exit.assert_called()


class TestOrchestratorOnlyFlag(unittest.TestCase):
    def test_only_is_threaded_through_to_engine_run(self):
        """--only (plan 060) must reach Engine.run, not get dropped along the way."""
        from crab.cli.orchestrator import execute_orchestrator

        preset_config = {"env": {}, "sbatch": [], "header": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"global_options": {"numnodes": "2"}, "experiments": {}}, f)

            with (
                patch("crab.cli.orchestrator.load_environment_config", return_value=preset_config),
                patch("crab.setup.memory.get_all_receipts", return_value={}),
                patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
                patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
            ):
                execute_orchestrator(config_path, "local", only=["ex1", "ex3"])

        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs.get("only"), ["ex1", "ex3"])


class TestWorkerCwdResolution(unittest.TestCase):
    def test_worker_resolves_cwd_placeholder_in_environment(self):
        """execute_worker must resolve __CWD__ in environment.json (audit W4 §1).

        Otherwise CRAB_ROOT reaches the engine as the literal string '__CWD__',
        silently breaking every wrapper that builds paths off os.environ['CRAB_ROOT'].
        This is the blocker for running the CRAB worker inside a SbatchMan allocation.
        """
        from crab.cli.orchestrator import CRAB_ROOT, execute_worker

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "config.json"), "w") as f:
                json.dump({"global_options": {"numnodes": "2"}, "experiments": {}}, f)
            with open(os.path.join(tmpdir, "environment.json"), "w") as f:
                json.dump({"CRAB_ROOT": "__CWD__", "CRAB_SYSTEM": "test"}, f)

            with patch("crab.core.engine.Engine.run", return_value={}) as mock_run:
                execute_worker(tmpdir)

            mock_run.assert_called_once()
            passed_env = mock_run.call_args.kwargs.get("environment")
            self.assertEqual(passed_env["CRAB_ROOT"], CRAB_ROOT)
            self.assertNotIn("__CWD__", passed_env["CRAB_ROOT"])


# ── Logger ────────────────────────────────────────────────────────────────────


class TestLoggerHandlerCopy(unittest.TestCase):
    def test_child_add_handler_does_not_mutate_parent(self):
        """enter() must pass a copy of handlers so child add_handler doesn't affect parent.

        The bug: parent._handlers is passed by reference; when the parent already
        has handlers (non-empty list, so `handlers or []` returns the same object),
        child.add_handler() mutates the parent's list.
        """
        from crab.log.logger import CrabLogger

        root_handler = MagicMock()
        # Parent must have at least one handler so 'handlers or []' doesn't trick us
        parent = CrabLogger(handlers=[root_handler])
        child = parent.enter("experiment")
        extra = MagicMock()
        child.add_handler(extra)
        self.assertNotIn(
            extra, parent._handlers, "add_handler on child must not mutate parent._handlers"
        )

    def test_two_children_do_not_share_handler_lists(self):
        """Two sibling children must not share each other's added handlers."""
        from crab.log.logger import CrabLogger

        root_handler = MagicMock()
        parent = CrabLogger(handlers=[root_handler])
        child_a = parent.enter("a")
        child_b = parent.enter("b")
        extra = MagicMock()
        child_a.add_handler(extra)
        # child_b must NOT see the handler added to child_a
        self.assertNotIn(
            extra, child_b._handlers, "Handlers added to child_a must not appear in child_b"
        )


# ── Memory ────────────────────────────────────────────────────────────────────


class TestMemoryAtomicSave(unittest.TestCase):
    def test_save_receipt_uses_atomic_replace(self):
        """save_receipt must use os.replace() for atomic write, not direct open(w)."""
        import crab.setup.memory as mem

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_env = mem.ENV_DIR
            mem.ENV_DIR = tmpdir
            try:
                with patch("os.replace") as mock_replace:
                    try:
                        mem.save_receipt("test_bench", {"binary_path": "/bin/test"})
                    except Exception:
                        pass
                mock_replace.assert_called_once()
            finally:
                mem.ENV_DIR = orig_env

    def test_save_receipt_file_exists_and_valid(self):
        """save_receipt must write a valid JSON file."""
        import crab.setup.memory as mem

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_env = mem.ENV_DIR
            mem.ENV_DIR = tmpdir
            try:
                receipt = {"binary_path": "/bin/test", "type": "binary"}
                mem.save_receipt("mybench", receipt)
                with open(os.path.join(tmpdir, "mybench.json")) as f:
                    loaded = json.load(f)
                self.assertEqual(loaded, receipt)
            finally:
                mem.ENV_DIR = orig_env

    def test_remove_receipt_no_raise_on_concurrent_deletion(self):
        """remove_receipt must not raise FileNotFoundError (TOCTOU fix).

        Bug: os.path.exists() check passes, then another process deletes the file,
        then os.remove() raises FileNotFoundError. Fix: try/except directly.
        Simulated by making os.remove always raise FileNotFoundError.
        """
        import crab.setup.memory as mem

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_env = mem.ENV_DIR
            mem.ENV_DIR = tmpdir
            try:
                receipt_path = os.path.join(tmpdir, "bench.json")
                with open(receipt_path, "w") as f:
                    json.dump({}, f)
                # Simulate the TOCTOU race: file exists at check time, gone at remove time
                with patch("os.remove", side_effect=FileNotFoundError):
                    try:
                        mem.remove_receipt("bench")
                    except FileNotFoundError:
                        self.fail("remove_receipt raised FileNotFoundError (TOCTOU not handled)")
            finally:
                mem.ENV_DIR = orig_env

    def test_remove_receipt_removes_existing_file(self):
        """remove_receipt must actually remove the file when it exists."""
        import crab.setup.memory as mem

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_env = mem.ENV_DIR
            mem.ENV_DIR = tmpdir
            try:
                receipt_path = os.path.join(tmpdir, "bench.json")
                with open(receipt_path, "w") as f:
                    json.dump({}, f)
                mem.remove_receipt("bench")
                self.assertFalse(os.path.exists(receipt_path))
            finally:
                mem.ENV_DIR = orig_env
