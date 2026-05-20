import os
import shutil
import subprocess
from typing import Tuple, Optional, Callable
from .base import BenchmarkRecipe

class QERecipe(BenchmarkRecipe):
    # Attributes injected by wizard.py
    arch: str = "cpu"
    pre_commands: list = []

    @property
    def name(self) -> str: return "Quantum ESPRESSO"
    @property
    def benchmark_id(self) -> str: return "quantum_espresso"
    @property
    def launcher_override(self) -> str: return "mpirun"

    def check_dependencies(self) -> Tuple[bool, str]:
        if not shutil.which("cmake"):
            return False, "CMake is required to build QE from source."
        return True, "Dependencies found."

    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        # Helper to run commands with the injected pre_commands
        def run_with_env(cmd_list, step_name):
            wrapped_cmd = ["bash", "-c", f"{' && '.join(self.pre_commands)} && {' '.join(cmd_list)}"]
            return self.run_command_streamed(wrapped_cmd, target_dir, step_name, log_callback)

        # 1. Clone
        repo_url = "https://gitlab.com/QEF/q-e.git"
        if not self.run_command_streamed(["git", "clone", repo_url, target_dir], ".", "Cloning Q-E...", log_callback):
            return False, "Clone failed."
            
        # 2. Build
        build_dir = os.path.join(target_dir, "build")
        os.makedirs(build_dir, exist_ok=True)
        
        cmake_flags = ["cmake", "..", "-DCMAKE_INSTALL_PREFIX=.."]
        if self.arch == "gpu":
            cmake_flags.append("-DQE_ENABLE_CUDA=ON")
            
        if not run_with_env(cmake_flags, "Configuring QE..."):
            return False, "CMake config failed."
            
        if not run_with_env(["make", "-j"], "Building QE..."):
            return False, "Build failed."
            
        return True, f"{os.path.join(target_dir, 'bin')}|{self.arch}"
