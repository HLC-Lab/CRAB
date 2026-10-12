"""stop_process_groups: SIGTERM every group, SIGKILL only the survivors after the grace.

Real signals go only to bash groups these tests start themselves; fabricated pids and mock
processes run with os.killpg mocked.
"""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from crab.core.process import stop_process_groups

# Each script touches $READY right after installing its trap; `_start` waits for that file.
_HONOURS_TERM = 'trap "echo term > $F; exit 0" TERM; echo > $READY; while :; do :; done'
_IGNORES_TERM = 'trap "" TERM; echo > $READY; while :; do :; done'
# srun-like: the leader exits on TERM while a member of its group ignores it.
_LEADER_EXITS_MEMBER_STAYS = (
    '(trap "" TERM; while :; do :; done) & trap "exit 0" TERM; echo > $READY; wait'
)


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _has_live_member(pgid: int) -> bool:
    """True if some non-zombie process is in the group (orphan zombies are not reaped in the sandbox)."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                fields = f.read().rpartition(")")[2].split()
        except OSError:
            continue
        if int(fields[2]) == pgid and fields[0] != "Z":
            return True
    return False


class _RealGroups(unittest.TestCase):
    def setUp(self):
        self._procs: list[subprocess.Popen] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        for p in self._procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()

    def _start(self, script: str, env: dict | None = None) -> subprocess.Popen:
        ready = os.path.join(self._tmp.name, f"ready-{len(self._procs)}")
        p = subprocess.Popen(
            ["bash", "-c", script],
            start_new_session=True,
            env={**os.environ, "READY": ready, **(env or {})},
        )
        self._procs.append(p)
        # TERM must not arrive before the trap is installed: wait for the ready file.
        deadline = time.monotonic() + 5.0
        while not os.path.exists(ready):
            if time.monotonic() > deadline:
                self.fail("child did not signal readiness within 5 s")
            time.sleep(0.02)
        return p


class TestRealGroups(_RealGroups):
    def test_term_honoured_needs_no_kill(self):
        marker = os.path.join(self._tmp.name, "term.txt")
        p = self._start(_HONOURS_TERM, {"F": marker})
        logger = MagicMock()
        t0 = time.monotonic()
        killed = stop_process_groups([p], grace=3, logger=logger)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.5)
        self.assertEqual(killed, [])
        with open(marker) as f:
            self.assertEqual(f.read().strip(), "term")
        self.assertTrue(_group_gone(p.pid))
        logger.warning.assert_not_called()

    def test_term_ignored_falls_back_to_kill(self):
        p = self._start(_IGNORES_TERM)
        logger = MagicMock()
        t0 = time.monotonic()
        killed = stop_process_groups([p], grace=0.5, logger=logger)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(killed, [p.pid])
        self.assertTrue(_group_gone(p.pid))
        logger.warning.assert_called()
        self.assertIn(str(p.pid), logger.warning.call_args[0][0])

    def test_several_groups_share_one_grace(self):
        polite = self._start(_HONOURS_TERM, {"F": os.path.join(self._tmp.name, "t.txt")})
        stubborn_a = self._start(_IGNORES_TERM)
        stubborn_b = self._start(_IGNORES_TERM)
        t0 = time.monotonic()
        killed = stop_process_groups([polite, stubborn_a, stubborn_b], grace=1.0)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0)  # one grace, not one per group
        self.assertEqual(sorted(killed), sorted([stubborn_a.pid, stubborn_b.pid]))
        for p in (polite, stubborn_a, stubborn_b):
            self.assertTrue(_group_gone(p.pid))

    def test_already_exited_process_is_fine(self):
        p = self._start("echo > $READY; exit 0")
        p.wait()
        killed = stop_process_groups([p], grace=1.0)
        self.assertEqual(killed, [])

    def test_leader_exits_but_member_ignores_term(self):
        """srun-like: the group leader dies on TERM, a member keeps running until KILL."""
        p = self._start(_LEADER_EXITS_MEMBER_STAYS)
        self.assertTrue(_has_live_member(p.pid))
        logger = MagicMock()
        t0 = time.monotonic()
        killed = stop_process_groups([p], grace=0.5, logger=logger)
        elapsed = time.monotonic() - t0
        self.assertEqual(killed, [p.pid])
        self.assertFalse(_has_live_member(p.pid))
        # A killed orphan can stay a zombie: that must not count as alive (no 2 s post-KILL wait).
        self.assertLess(elapsed, 1.5)
        for call in logger.warning.call_args_list:
            self.assertNotIn("still alive", call[0][0])


class TestMocked(unittest.TestCase):
    def test_gone_group_is_not_signalled_again(self):
        proc = MagicMock(pid=424242)
        proc.poll.return_value = 0
        with patch("crab.core.process.manager.os.killpg") as killpg:
            killpg.side_effect = ProcessLookupError
            killed = stop_process_groups([proc], grace=0.2)
        self.assertEqual(killed, [])
        # Only the first TERM attempt; no liveness probe or KILL after it.
        self.assertEqual(killpg.call_count, 1)
        self.assertEqual(killpg.call_args[0], (424242, signal.SIGTERM))

    def test_refuses_pid_one_and_own_group(self):
        logger = MagicMock()
        one = MagicMock(pid=1)
        own = MagicMock(pid=os.getpgrp())
        with patch("crab.core.process.manager.os.killpg") as killpg:
            killed = stop_process_groups([one, own], grace=0.2, logger=logger)
        killpg.assert_not_called()
        self.assertEqual(killed, [])
        self.assertEqual(logger.warning.call_count, 2)

    def test_refuses_pid_zero_and_negative(self):
        logger = MagicMock()
        with patch("crab.core.process.manager.os.killpg") as killpg:
            stop_process_groups([MagicMock(pid=0), MagicMock(pid=-5)], grace=0.2, logger=logger)
        killpg.assert_not_called()
        self.assertEqual(logger.warning.call_count, 2)

    def test_permission_error_on_probe_is_logged(self):
        logger = MagicMock()
        proc = MagicMock(pid=424242)

        def fake_killpg(g, sig):
            if sig == 0:
                raise PermissionError

        with patch("crab.core.process.manager.os.killpg", side_effect=fake_killpg):
            killed = stop_process_groups([proc], grace=0.2, logger=logger)
        self.assertEqual(killed, [])
        logger.warning.assert_called()
        self.assertIn("424242", logger.warning.call_args[0][0])

    def test_permission_error_is_skipped(self):
        logger = MagicMock()
        proc = MagicMock(pid=424242)
        with patch("crab.core.process.manager.os.killpg", side_effect=PermissionError) as killpg:
            killed = stop_process_groups([proc], grace=0.2, logger=logger)
        self.assertEqual(killed, [])
        self.assertEqual(killpg.call_count, 1)
        logger.warning.assert_called()

    def test_term_first_kill_last_for_same_group(self):
        pgid = 424242
        proc = MagicMock(pid=pgid)
        proc.poll.return_value = None
        calls: list[tuple[int, int]] = []
        killed_flag = []

        def fake_killpg(g, sig):
            calls.append((g, sig))
            if sig == signal.SIGKILL:
                killed_flag.append(True)
            elif sig == 0 and killed_flag:
                raise ProcessLookupError

        with patch("crab.core.process.manager.os.killpg", side_effect=fake_killpg):
            killed = stop_process_groups([proc], grace=0.2)
        self.assertEqual(killed, [pgid])
        sigs = [s for g, s in calls if s != 0]
        self.assertEqual(sigs, [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(all(g == pgid for g, _ in calls))
        self.assertEqual(calls[0], (pgid, signal.SIGTERM))
        self.assertEqual(calls[-1][1], 0)  # final probe confirming it is gone


if __name__ == "__main__":
    unittest.main()
