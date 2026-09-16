"""
core/wl_manager/local.py: no-launcher workload manager for CRAB_SCHEDULER=local (plan 087).

Single-process, no MPI (owner's explicit "skip MPI" answer) — run_job must return the raw
command unchanged regardless of node_list/ppn/launcher, unlike slurm.py which prefixes an
srun/mpirun launcher line.
"""

import unittest

from crab.core.wl_manager.local import wl_manager


class TestLocalWlManager(unittest.TestCase):
    def test_run_job_returns_command_unchanged(self):
        wlm = wl_manager()
        result = wlm.run_job(node_list=["localhost"], ppn=1, cmd="echo hello")
        self.assertEqual(result, "echo hello")

    def test_run_job_ignores_node_list_ppn_and_launcher(self):
        """No srun/mpirun prefix, no matter what node_list/ppn/launcher are passed."""
        wlm = wl_manager()
        result = wlm.run_job(
            node_list=["node01", "node02"], ppn=8, cmd="python wrapper.py", launcher="mpirun"
        )
        self.assertEqual(result, "python wrapper.py")
        self.assertNotIn("srun", result)
        self.assertNotIn("mpirun", result)


if __name__ == "__main__":
    unittest.main()
