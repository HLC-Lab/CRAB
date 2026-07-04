"""Local-only tests for HIGH-priority manager.py issues."""

import subprocess
import threading
import unittest
from unittest.mock import MagicMock, patch

from crab.core.process import wait_timed


class _MockJob:
    def __init__(self, stdout_buf=None, stderr_data=b""):
        self.process = MagicMock()
        self.process.wait = MagicMock(return_value=0)
        self.process.returncode = 0
        self.process.stderr = MagicMock()
        self.process.stderr.read = MagicMock(return_value=stderr_data)
        self.raw_stdout_buffer = stdout_buf if stdout_buf is not None else [b"hello\n"]
        self._stream_thread = None
        self._set_output_calls = []

    def set_output(self, out, err):
        self._set_output_calls.append((out, err))


class TestWaitTimedNoCommunicate(unittest.TestCase):
    def test_communicate_never_called(self):
        """wait_timed must NOT call communicate() — that races with _silent_reader."""
        job = _MockJob()
        wait_timed(job, timeout_sec=5.0, logger=MagicMock())
        job.process.communicate.assert_not_called()

    def test_calls_process_wait(self):
        """wait_timed must call process.wait() so it doesn't compete for stdout."""
        job = _MockJob()
        wait_timed(job, timeout_sec=5.0, logger=MagicMock())
        job.process.wait.assert_called_once()

    def test_joins_stream_thread(self):
        """wait_timed must join _stream_thread before reconstructing output."""
        job = _MockJob()
        joined = []
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        orig = t.join
        t.join = lambda *a, **kw: (joined.append(True), orig(*a, **kw))
        job._stream_thread = t
        wait_timed(job, timeout_sec=5.0, logger=MagicMock())
        self.assertTrue(joined, "_stream_thread.join() must be called")

    def test_reconstructs_stdout_from_buffer(self):
        """wait_timed must build stdout from raw_stdout_buffer, not communicate."""
        job = _MockJob(stdout_buf=[b"line1\n", b"line2\n"])
        wait_timed(job, timeout_sec=5.0, logger=MagicMock())
        out, _ = job._set_output_calls[0]
        self.assertEqual(out, b"line1\nline2\n")

    def test_returns_true_on_timeout(self):
        """wait_timed must return True when the process times out."""
        job = _MockJob()
        job.process.wait.side_effect = subprocess.TimeoutExpired(cmd="bash", timeout=0.001)
        with patch("crab.core.process.manager.end_job"):
            result = wait_timed(job, timeout_sec=0.001, logger=MagicMock())
        self.assertTrue(result)

    def test_returns_false_on_normal_exit(self):
        """wait_timed must return False when the process exits normally."""
        job = _MockJob()
        result = wait_timed(job, timeout_sec=5.0, logger=MagicMock())
        self.assertFalse(result)
