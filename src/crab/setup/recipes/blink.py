import os
import shutil
from collections.abc import Callable

from .base import BenchmarkRecipe, BuildResult


class BlinkRecipe(BenchmarkRecipe):
    @property
    def name(self) -> str:
        return "Blink Suite"

    @property
    def benchmark_id(self) -> str:
        return "blink"

    def check_dependencies(self, env: dict[str, str]) -> tuple[bool, str]:
        if not shutil.which("make", path=env.get("PATH")):
            return False, "Make is missing from current path context."
        if not shutil.which("mpicc", path=env.get("PATH")):
            return False, "MPI compiler (mpicc) missing inside target environment module."
        return True, "Dependencies found."

    def download_and_build(
        self,
        target_dir: str,
        params: dict[str, str],
        env: dict[str, str],
        log_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[bool, BuildResult | None, str]:
        repo_url = "https://github.com/SharkGamerZ/blink-clean.git"
        if not self.run_command_streamed(
            ["git", "clone", repo_url, target_dir], ".", "Cloning Repository...", env, log_callback
        ):
            return False, None, "Git clone failed."

        bin_dir = os.path.join(target_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)

        if not self.run_command_streamed(
            ["make", "CC=mpicc", "CXX=mpicxx", "-j"],
            target_dir,
            "Compiling Binaries...",
            env,
            log_callback,
        ):
            return False, None, "Make compilation failed."

        expected_binary = os.path.join(bin_dir, "ping-pong_b")
        if os.path.exists(expected_binary):
            return True, BuildResult(binary_path=bin_dir), "Blink suite built successfully."

        return False, None, "Compilation finished, but executables were missing from bin/."

    def verify_existing(self, path: str) -> bool:
        if not os.path.isdir(path):
            return False
        test_binary = os.path.join(path, "ping-pong_b")
        return os.path.isfile(test_binary) and os.access(test_binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> str | None:
        local_target = os.path.join(crab_benchmarks_dir, "blink", "bin")
        if self.verify_existing(local_target):
            return local_target
        return None
