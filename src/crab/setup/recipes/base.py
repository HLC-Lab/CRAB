import os
import shutil
from abc import ABC, abstractmethod
from typing import Optional, Tuple

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
    def download_and_build(self, target_dir: str) -> Tuple[bool, str]:
        """
        Logic to clone and compile the benchmark.
        Args:
            target_dir (str): Typically CRAB_ROOT/benchmarks/<benchmark_name>.
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
        Provides a default implementation that checks the local benchmarks folder
        and the system $PATH. Child classes can override this if they need to look
        in specific HPC directories like /opt/ or /usr/local/.
        """
        # Guess the binary name based on the env_key (e.g., CRAB_G500_PATH -> g500)
        # Recipes can override this method entirely if this assumption is wrong.
        binary_name = self.env_key.replace("CRAB_", "").replace("_PATH", "").lower()
        
        # 1. Check local CRAB installations first
        local_target = os.path.join(crab_benchmarks_dir, binary_name)
        if self.verify_existing(local_target):
            return local_target
            
        # 2. Check standard system $PATH
        system_path = shutil.which(binary_name)
        if system_path and self.verify_existing(system_path):
            return system_path
            
        return None
