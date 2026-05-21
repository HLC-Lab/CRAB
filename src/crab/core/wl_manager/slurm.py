import os
from typing import List, Optional

class wl_manager:
    def run_job(self, node_list: List[str], ppn: int, cmd: str, pre_commands: Optional[List[str]] = None, data_path: str = None, launcher: Optional[str] = None) -> str:
        num_nodes = len(node_list)
        total_tasks = ppn * num_nodes

        # --- LAUNCHER SELECTION & FORMATTING ---
        actual_launcher = launcher or os.environ.get("CRAB_MPIRUN", "srun")
        actual_launcher = actual_launcher.strip()

        if "mpirun" in actual_launcher:
            # mpirun inside SLURM auto-detects nodes, but needs the total tasks (-np)
            additional_flags = os.environ.get("CRAB_MPIRUN_ADDITIONAL_FLAGS", "")
            map_by = os.environ.get("CRAB_MPIRUN_MAP_BY_NODE_FLAG", "")
            slurm_string = f"{actual_launcher} {additional_flags} {map_by} -np {total_tasks} {cmd}"
        else:
            # Default srun behavior
            node_list_string = ','.join(node_list)
            node_list_arg = '--nodelist ' + node_list_string
            pinning = os.environ.get("CRAB_PINNING_FLAGS", "")
            slurm_string = f"{actual_launcher} --export=ALL {node_list_arg} {pinning} -n {total_tasks} -N {num_nodes} {cmd}"

        # Clean up multiple spaces
        return " ".join(slurm_string.split())
