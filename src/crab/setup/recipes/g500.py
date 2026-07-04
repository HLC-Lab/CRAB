import os
import shutil
from collections.abc import Callable

from .base import BenchmarkRecipe, BuildResult


class G500Recipe(BenchmarkRecipe):
    @property
    def name(self) -> str:
        return "Graph500"

    @property
    def benchmark_id(self) -> str:
        return "g500"

    def check_dependencies(self, env: dict[str, str]) -> tuple[bool, str]:
        if not shutil.which("mpicc", path=env.get("PATH")):
            return False, "MPI compiler (mpicc) not found inside target environment module."
        if not shutil.which("make", path=env.get("PATH")):
            return False, "Make is missing."
        return True, "Dependencies found."

    def download_and_build(
        self,
        target_dir: str,
        params: dict[str, str],
        env: dict[str, str],
        log_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[bool, BuildResult | None, str]:
        repo_url = "https://github.com/graph500/graph500.git"
        if not self.run_command_streamed(
            ["git", "clone", repo_url, target_dir], ".", "Cloning Repository...", env, log_callback
        ):
            return False, None, "Git clone failed."

        src_dir = os.path.join(target_dir, "src")
        build_cmd = ["make", "MPICC=mpicc", "CFLAGS=-fcommon", "-j"]

        if not self.run_command_streamed(
            build_cmd, src_dir, "Compiling Binaries...", env, log_callback
        ):
            return False, None, "Make compilation failed."

        binary_path = os.path.join(src_dir, "graph500_reference_bfs")
        if os.path.exists(binary_path):
            return True, BuildResult(binary_path=src_dir), "Graph500 built successfully."

        return False, None, "Compilation finished, but output binaries were missing."

    def verify_existing(self, path: str) -> bool:
        if not os.path.isdir(path):
            return False
        test_binary = os.path.join(path, "graph500_reference_bfs")
        return os.path.isfile(test_binary) and os.access(test_binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> str | None:
        local_target = os.path.join(crab_benchmarks_dir, "g500", "src")
        if self.verify_existing(local_target):
            return local_target
        return None
