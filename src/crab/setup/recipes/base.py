import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Callable, List

class BenchmarkRecipe(ABC):
    """
    The strict contract for all CRAB benchmark installation recipes.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The display name of the benchmark shown in the UI (e.g., 'Graph500')."""
        pass

    @property
    @abstractmethod
    def benchmark_id(self) -> str:
        """The unique identifier for the receipt (e.g., 'g500', 'quantum_espresso')."""
        pass

    @property
    def launcher_override(self) -> str:
        """Override the cluster's default launcher (e.g., return 'mpirun' instead of 'srun')."""
        return ""

    @property
    def pre_run_hooks(self) -> List[str]:
        """Commands to run before the benchmark executes (e.g., specific exports)."""
        return []

    @abstractmethod
    def check_dependencies(self) -> Tuple[bool, str]:
        """Pre-flight check before building from source."""
        pass

    @abstractmethod
    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Logic to clone and compile the benchmark."""
        pass

    @abstractmethod
    def verify_existing(self, path: str) -> bool:
        """Validates if the user-provided path actually contains the expected, executable binary."""
        pass

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        """Tier 1 Auto-Detect."""
        binary_name = self.benchmark_id.lower()
        
        local_target = os.path.join(crab_benchmarks_dir, binary_name)
        if self.verify_existing(local_target):
            return local_target
            
        system_path = shutil.which(binary_name)
        if system_path and self.verify_existing(system_path):
            return system_path
            
        return None

    def run_command_streamed(self, cmd: list, cwd: str, step_name: str, log_callback: Optional[Callable[[str, str], None]]) -> bool:
        """Runs a shell command and streams the output."""
        if log_callback:
            log_callback("step", step_name)
            
        try:
            process = subprocess.Popen(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, bufsize=1
            )
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if log_callback and line.strip():
                        log_callback("log", line.strip())
            process.wait()
            return process.returncode == 0
        except Exception as e:
            if log_callback:
                log_callback("log", f"CRITICAL ERROR: {str(e)}")
            return False
