from __future__ import annotations

import os
import shutil
from collections.abc import Callable

from .base import BenchmarkRecipe, BuildResult

# Upstream builds with CMake and puts every executable in <build>/bin, named after its source file.
_BUILD_SUBDIR = "build"
_PROBE_BINARY = "pingpong_b"


class BlinkRecipe(BenchmarkRecipe):
    @property
    def name(self) -> str:
        return "Blink Suite"

    @property
    def benchmark_id(self) -> str:
        return "blink"

    def check_dependencies(self, env: dict[str, str]) -> tuple[bool, str]:
        if not shutil.which("cmake", path=env.get("PATH")):
            return False, "CMake (3.16 or newer) is missing from the current path context."
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
        repo_url = "https://github.com/HLC-Lab/blink.git"
        if not self.run_command_streamed(
            ["git", "clone", repo_url, target_dir], ".", "Cloning Repository...", env, log_callback
        ):
            return False, None, "Git clone failed."

        build_dir = os.path.join(target_dir, _BUILD_SUBDIR)
        # BLINK_TESTS defaults ON and fetches googletest from GitHub; the MPI wrapper option is
        # Open MPI-only (--showme), so it stays off.
        configure_cmd = [
            "cmake",
            "-S",
            target_dir,
            "-B",
            build_dir,
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBLINK_TESTS=OFF",
        ]
        build_env = {**env, "CC": "mpicc", "CXX": "mpicxx"}
        if not self.run_command_streamed(
            configure_cmd, target_dir, "Configuring with CMake...", build_env, log_callback
        ):
            return False, None, "CMake configure failed."

        if not self.run_command_streamed(
            ["cmake", "--build", build_dir, "-j"],
            target_dir,
            "Compiling Binaries...",
            build_env,
            log_callback,
        ):
            return False, None, "CMake build failed."

        bin_dir = os.path.join(build_dir, "bin")
        if os.path.exists(os.path.join(bin_dir, _PROBE_BINARY)):
            return True, BuildResult(binary_path=bin_dir), "Blink suite built successfully."

        return False, None, "Compilation finished, but executables were missing from build/bin/."

    def verify_existing(self, path: str) -> bool:
        if not os.path.isdir(path):
            return False
        test_binary = os.path.join(path, _PROBE_BINARY)
        return os.path.isfile(test_binary) and os.access(test_binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> str | None:
        local_target = os.path.join(crab_benchmarks_dir, "blink", _BUILD_SUBDIR, "bin")
        if self.verify_existing(local_target):
            return local_target
        return None
