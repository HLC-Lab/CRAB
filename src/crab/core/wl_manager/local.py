from __future__ import annotations


class wl_manager:
    """No-launcher workload manager for CRAB_SCHEDULER=local (plan 087, dev/testing-only).

    Single-process, no MPI: runs the wrapper command directly, no srun/mpirun prefix.
    """

    def run_job(
        self,
        node_list: list[str],
        ppn: int,
        cmd: str,
        pre_commands: list[str] | None = None,
        data_path: str = None,
        launcher: str | None = None,
    ) -> str:
        return cmd
