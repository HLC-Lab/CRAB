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
            
        # 2. Compile inside the src/ folder with injected flags
        # Graph500 3.0 uses the MPICC variable in its Makefile. 
        # By setting MPICC="mpicc -fcommon", we safely fix the GCC 10 linker error 
        # without overwriting the Makefile's internal CFLAGS.
        src_dir = os.path.join(target_dir, "src")
        build_cmd = ["make", "MPICC=mpicc -fcommon", "-j"]
        
        if not self.run_command_streamed(build_cmd, cwd=src_dir, step_name="Compiling Binaries...", log_callback=log_callback):
            return False, "Make compilation failed."
            
        # 3. Verify Output
        binary_path = os.path.join(src_dir, "graph500_reference_bfs")
        if os.path.exists(binary_path):
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
