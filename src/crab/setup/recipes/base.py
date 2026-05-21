import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Callable, List, Dict, Any
from dataclasses import dataclass, field

@dataclass
class BuildParameter:
    name: str
    description: str
    choices: Optional[List[str]] = None
    default: str = ""

@dataclass
class BuildManifest:
    """Declares what a recipe requires from the user/system to build."""
    requires_modules: bool = True
    parameters: List[BuildParameter] = field(default_factory=list)

@dataclass
class BuildResult:
    """Unified return schema for source compilations."""
    binary_path: str
    metadata: Dict[str, Any] = field(default_factory=dict)

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
        """Override the cluster's default launcher (e.g., 'mpirun')."""
        return ""

    @property
    def pre_run_hooks(self) -> List[str]:
        """Commands to run before the benchmark executes (e.g., specific exports)."""
        return []

    @property
    def build_manifest(self) -> BuildManifest:
        """Declares dynamic input requirements. Default requires modules, no parameters."""
        return BuildManifest()

    @abstractmethod
    def check_dependencies(self, env: Dict[str, str]) -> Tuple[bool, str]:
        """Pre-flight check before building, evaluating a modified environment context."""
        pass

    @abstractmethod
    def download_and_build(
        self, 
        target_dir: str, 
        params: Dict[str, str], 
        env: Dict[str, str], 
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Tuple[bool, Optional[BuildResult], str]:
        """Logic to clone and compile the benchmark using context-aware environment maps."""
        pass

    @abstractmethod
    def verify_existing(self, path: str) -> bool:
        """Validates if the target path contains the expected executable/directory structure."""
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

    def run_command_streamed(
        self, 
        cmd: List[str], 
        cwd: str, 
        step_name: str, 
        env: Optional[Dict[str, str]], 
        log_callback: Optional[Callable[[str, str], None]]
    ) -> bool:
        """Runs a command natively and streams output using an explicit environment map."""
        if log_callback:
            log_callback("step", step_name)
            
        try:
            process = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
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
