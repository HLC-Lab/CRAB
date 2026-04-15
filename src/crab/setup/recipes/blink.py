import os
import subprocess
import shutil
from typing import Tuple
from .base import BenchmarkRecipe

class BlinkRecipe(BenchmarkRecipe):
    
    @property
    def name(self) -> str:
        return "Blink"

    @property
    def env_key(self) -> str:
        return "CRAB_BLINK_PATH"

    def check_dependencies(self) -> Tuple[bool, str]:
        if not shutil.which("cmake"):
            return False, "CMake is missing. Suggestion: `module load cmake`"
        if not shutil.which("g++") and not shutil.which("icpc"):
            return False, "C++ compiler missing. Suggestion: `module load gcc`"
        return True, "Dependencies found."

    def download_and_build(self, target_dir: str) -> Tuple[bool, str]:
        try:
            # Clone
            repo_url = "https://github.com/HLC-Lab/blink.git"
            subprocess.run(["git", "clone", repo_url, target_dir], check=True, capture_output=True)
            
            # Setup Build Dir
            build_dir = os.path.join(target_dir, "build")
            os.makedirs(build_dir, exist_ok=True)
            
            # CMake & Make
            subprocess.run(["cmake", ".."], cwd=build_dir, check=True, capture_output=True)
            subprocess.run(["make", "-j"], cwd=build_dir, check=True, capture_output=True)
            
            # Verify Output
            binary_path = os.path.join(build_dir, "blink")
            if os.path.exists(binary_path):
                return True, binary_path
            return False, "Compilation finished, but 'blink' binary was not found in build/."
            
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
            return False, f"Build command failed:\n{err_msg}"
        except Exception as e:
            return False, f"Unexpected error during build: {str(e)}"

    def verify_existing(self, path: str) -> bool:
        return os.path.isfile(path) and os.access(path, os.X_OK)
