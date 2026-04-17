import os
import subprocess
import shutil
from typing import Tuple, Optional, Callable
from .base import BenchmarkRecipe

class G500Recipe(BenchmarkRecipe):
    
    @property
    def name(self) -> str:
        return "Graph500"

    @property
    def env_key(self) -> str:
        return "CRAB_G500_PATH"

    def check_dependencies(self) -> Tuple[bool, str]:
        if not shutil.which("mpicc"):
            return False, "MPI compiler (mpicc) not found. Suggestion: `module load openmpi`"
        if not shutil.which("make"):
            return False, "Make is missing."
        return True, "Dependencies found."

    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        # 1. Clone the repository
        repo_url = "https://github.com/graph500/graph500.git"
        if not self.run_command_streamed(["git", "clone", repo_url, target_dir], cwd=".", step_name="Cloning Repository...", log_callback=log_callback):
            return False, "Git clone failed."
            
        # 2. Generate the mandatory make.inc file in the root folder
        make_inc_path = os.path.join(target_dir, "make.inc")
        make_inc_content = "CC = mpicc\nCFLAGS = -O3 -std=c99 -Wall\nLDLIBS = -lm\n"
        try:
            with open(make_inc_path, "w") as f:
                f.write(make_inc_content)
            if log_callback:
                log_callback("log", "Generated make.inc with mpicc bindings.")
        except Exception as e:
            return False, f"Failed to generate make.inc: {e}"
            
        # 3. Compile inside the src/ folder
        src_dir = os.path.join(target_dir, "src")
        if not self.run_command_streamed(["make", "-j"], cwd=src_dir, step_name="Compiling Binaries...", log_callback=log_callback):
            return False, "Make compilation failed."
            
        # 4. Verify Output
        binary_path = os.path.join(src_dir, "graph500_reference_bfs")
        if os.path.exists(binary_path):
            # Return the DIRECTORY, not the single file
            return True, src_dir
            
        return False, "Compilation finished, but 'graph500_reference_bfs' was not found in src/."

    def verify_existing(self, path: str) -> bool:
        """
        Verify that the path is a directory and contains the G500 binary.
        """
        if not os.path.isdir(path):
            return False
            
        test_binary = os.path.join(path, "graph500_reference_bfs")
        return os.path.isfile(test_binary) and os.access(test_binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        """
        Look for the 'src' directory of graph500.
        """
        local_target = os.path.join(crab_benchmarks_dir, "graph500", "src")
        if self.verify_existing(local_target):
            return local_target
            
        return None
