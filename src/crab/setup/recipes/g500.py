import os
import subprocess
import shutil
from typing import Tuple, Optional
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

    def download_and_build(self, target_dir: str) -> Tuple[bool, str]:
        try:
            repo_url = "https://github.com/graph500/graph500.git"
            subprocess.run(["git", "clone", repo_url, target_dir], check=True, capture_output=True)
            
            # G500 uses standard make inside the /src folder
            src_dir = os.path.join(target_dir, "src")
            subprocess.run(["make", "-j"], cwd=src_dir, check=True, capture_output=True)
            
            binary_path = os.path.join(src_dir, "graph500_reference_bfs")
            if os.path.exists(binary_path):
                return True, binary_path
            return False, "Compilation finished, but 'graph500_reference_bfs' was not found."
            
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
            return False, f"Build command failed:\n{err_msg}"
        except Exception as e:
            return False, f"Unexpected error during build: {str(e)}"

    def verify_existing(self, path: str) -> bool:
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        """
        OVERRIDE: Graph500's binary is not named 'g500', so we must specify it.
        """
        expected_binary = "graph500_reference_bfs"
        
        # 1. Check local CRAB installations first
        local_target = os.path.join(crab_benchmarks_dir, "graph500", "src", expected_binary)
        if self.verify_existing(local_target):
            return local_target
            
        # 2. Check system PATH
        system_path = shutil.which(expected_binary)
        if system_path and self.verify_existing(system_path):
            return system_path
            
        return None
