import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Callable

class BenchmarkRecipe(ABC):
    """
    The strict contract for all CRAB benchmark installation recipes.
    Any new benchmark added to CRAB must inherit from this class.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The display name of the benchmark shown in the UI (e.g., 'Graph500')."""
        pass

    @property
    @abstractmethod
    def env_key(self) -> str:
        """The environment variable key used in CRAB memory (e.g., 'CRAB_G500_PATH')."""
        pass

    @abstractmethod
    def check_dependencies(self) -> Tuple[bool, str]:
        """
        Pre-flight check before building from source.
        Returns:
            Tuple[bool, str]: (True, "Success message") or (False, "Error/Module load suggestion").
        """
        pass

    @abstractmethod
    def download_and_build(self, target_dir: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """
        Logic to clone and compile the benchmark.
        Args:
            target_dir (str): Typically CRAB_ROOT/benchmarks/<benchmark_name>.
            log_callback (Callable): Pass real-time updates back to the UI. 
                                     Takes (msg_type: "step" | "log", message: str).
        Returns:
            Tuple[bool, str]: (True, absolute_path_to_binary) or (False, error_message).
        """
        pass

    @abstractmethod
    def verify_existing(self, path: str) -> bool:
        """
        Validates if the user-provided path actually contains the expected, executable binary.
        """
        pass

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        """
        Tier 1 Auto-Detect. 
        """
        binary_name = self.env_key.replace("CRAB_PATH_", "").lower()
        
        local_target = os.path.join(crab_benchmarks_dir, binary_name)
        if self.verify_existing(local_target):
            return local_target
            
        system_path = shutil.which(binary_name)
        if system_path and self.verify_existing(system_path):
            return system_path
            
        return None

    def run_command_streamed(self, cmd: list, cwd: str, step_name: str, log_callback: Optional[Callable[[str, str], None]]) -> bool:
        """
        A built-in helper method for recipes. Runs a shell command, reads it line by line, 
        and streams the output back to the UI via the log_callback.
        """
        if log_callback:
            log_callback("step", step_name)
            
        try:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                bufsize=1  # Line buffered
            )
            
            # Stream output in real-time
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
