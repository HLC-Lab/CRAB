import os
import shutil
from typing import Tuple, Optional, Callable
from .base import BenchmarkRecipe

class BlinkRecipe(BenchmarkRecipe):
    
    @property
    def name(self) -> str:
        return "Blink Suite"

    @property
    def env_key(self) -> str:
        return "CRAB_PATH_BLINK"

    def check_dependencies(self) -> Tuple[bool, str]:
        if not shutil.which("make"):
            return False, "Make is missing."
        if not shutil.which("mpicc"):
            return False, "MPI compiler (mpicc) missing. Suggestion: `module load openmpi` or your cluster's MPI module."
        return True, "Dependencies found."

    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        # 1. Clone the new blink-clean repo
        repo_url = "https://github.com/SharkGamerZ/blink-clean.git"
        if not self.run_command_streamed(["git", "clone", repo_url, target_dir], cwd=".", step_name="Cloning Repository...", log_callback=log_callback):
            return False, "Git clone failed."
            
        # 2. Ensure bin/ directory exists (git doesn't track empty folders, and Make might fail without it)
        bin_dir = os.path.join(target_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)

        # 3. Compile using raw Make, explicitly injecting mpicc
        if not self.run_command_streamed(["make", "CC=mpicc", "-j"], cwd=target_dir, step_name="Compiling Binaries...", log_callback=log_callback):
            return False, "Make compilation failed."
            
        # 4. Verify Output (Check if at least one expected binary was created)
        expected_binary = os.path.join(bin_dir, "ping-pong_b")
        if os.path.exists(expected_binary):
            # Return the path to the bin DIRECTORY, not a single file
            return True, bin_dir
            
        return False, "Compilation finished, but executables were not found in bin/."

    def verify_existing(self, path: str) -> bool:
        """
        Since Blink is a suite, we verify that the path is a directory
        and contains typical Blink executables.
        """
        if not os.path.isdir(path):
            return False
            
        # Check if it contains at least one known Blink executable
        test_binary = os.path.join(path, "ping-pong_b")
        return os.path.isfile(test_binary) and os.access(test_binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        """
        Override fast_search to look for the 'bin' directory of blink.
        """
        # Check local CRAB installations first
        local_target = os.path.join(crab_benchmarks_dir, "blink_suite", "bin")
        if self.verify_existing(local_target):
            return local_target
            
        return None
