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

    def verify_existing(self, path: str) -> bool:
        return os.path.exists(os.path.join(path, "pw.x"))

    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        # 1. Clone
        repo_url = "https://gitlab.com/QEF/q-e.git"
        # Clone directly into target_dir
        if not self.run_command_streamed(["git", "clone", repo_url, target_dir], ".", "Cloning Q-E...", log_callback):
            return False, "Clone failed."
            
        # 2. Build
        build_dir = os.path.join(target_dir, "build")
        os.makedirs(build_dir, exist_ok=True)
        
        # Helper to run commands
        def run_with_env(cmd_list, step_name, working_dir):
            # Wrap in bash -c so 'module' function is available
            wrapped_cmd = ["bash", "-c", f"{' && '.join(self.pre_commands)} && {' '.join(cmd_list)}"]
            return self.run_command_streamed(wrapped_cmd, working_dir, step_name, log_callback)

        cmake_flags = [
                "cmake", "..", 
                "-DCMAKE_INSTALL_PREFIX=..",
                "-DCMAKE_C_COMPILER=mpicc",
                "-DCMAKE_Fortran_COMPILER=mpif90",
                "-DQE_ENABLE_OPENMP=ON",
                "-DQE_ENABLE_MPI=ON",
                "-DQE_FFTW_VENDOR=Internal"
                ]

        if self.arch == "gpu":
            cmake_flags.append("-DQE_ENABLE_CUDA=ON")
            
        if not run_with_env(cmake_flags, "Configuring QE...", build_dir):
            return False, "CMake config failed."
            
        if not run_with_env(["make", "-j"], "Building QE...", build_dir):
            return False, "Build failed."
            
        return True, f"{os.path.join(target_dir, 'bin')}|{self.arch}"
