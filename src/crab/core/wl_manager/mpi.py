# ---------------------------------------------------------------------------
# UNPORTED / LEGACY workload manager.
# This module has NOT been ported to the current run_job() interface used by
# core/process/manager.py, which passes a `launcher` argument (see slurm.py for
# the ported reference implementation). Until it is ported it raises
# NotImplementedError if invoked. See fix_plan.md.
# ---------------------------------------------------------------------------


class wl_manager:
    # Returns a string that can be used to run command 'cmd'
    # on the nodes in 'node_list' with 'ppn' processes per node.
    def run_job(self, node_list, ppn, cmd, pre_commands=None, data_path=None, launcher=None):
        raise NotImplementedError(
            "The 'mpi' workload manager has not been ported to the current "
            "run_job(launcher=...) interface. Use the 'slurm' workload manager, "
            "or port this module (see fix_plan.md)."
        )
