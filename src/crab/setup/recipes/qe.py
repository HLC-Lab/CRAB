import os
import shutil
from typing import Tuple, Optional, Callable
from .base import BenchmarkRecipe
from rich.prompt import Prompt

class QERecipe(BenchmarkRecipe):
    
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
        # User explicitly chooses ONE architecture
        arch = Prompt.ask("Select Build Architecture", choices=["cpu", "gpu"], default="cpu")
        
        repo_url = "https://gitlab.com/QEF/q-e.git"
        if not self.run_command_streamed(["git", "clone", repo_url, target_dir], ".", "Cloning Q-E...", log_callback):
            return False, "Clone failed."
            
        build_dir = os.path.join(target_dir, "build")
        os.makedirs(build_dir, exist_ok=True)
        
        cmake_flags = ["cmake", "..", "-DCMAKE_INSTALL_PREFIX=.."]
        if arch == "gpu":
            cmake_flags.append("-DQE_ENABLE_CUDA=ON")
            
        if not self.run_command_streamed(cmake_flags, build_dir, f"Configuring for {arch}...", log_callback):
            return False, "CMake config failed."
            
        if not self.run_command_streamed(["make", "-j"], build_dir, "Compiling...", log_callback):
            return False, "Build failed."
            
        # Returns format: "/path/to/bin|arch"
        return True, f"{os.path.join(target_dir, 'bin')}|{arch}"

    def verify_existing(self, path: str) -> bool:
        check_path = path.split('|')[0]
        return os.path.exists(os.path.join(check_path, "pw.x"))
