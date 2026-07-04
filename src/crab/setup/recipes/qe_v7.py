import os
import shutil
from collections.abc import Callable

from .base import BenchmarkRecipe, BuildManifest, BuildParameter, BuildResult


class QERecipeV7(BenchmarkRecipe):
    @property
    def name(self) -> str:
        return "Quantum ESPRESSO v7"

    @property
    def suite(self) -> str:
        return "Quantum ESPRESSO"

    @property
    def benchmark_id(self) -> str:
        return "qe-v7"

    @property
    def launcher_override(self) -> str:
        return "mpirun"

    @property
    def build_manifest(self) -> BuildManifest:
        return BuildManifest(
            requires_modules=True,
            parameters=[
                BuildParameter(
                    name="arch",
                    description="Select Target Architecture",
                    choices=["cpu", "gpu"],
                    default="cpu",
                )
            ],
        )

    def check_dependencies(self, env: dict[str, str]) -> tuple[bool, str]:
        if not shutil.which("cmake", path=env.get("PATH")):
            return False, "CMake is required to build QE from source."
        return True, "Dependencies found."

    def fast_search(self, crab_benchmarks_dir: str) -> str | None:
        local_target = os.path.join(crab_benchmarks_dir, self.benchmark_id)
        build_bin = os.path.join(local_target, "build", "bin")
        if os.path.exists(os.path.join(build_bin, "pw.x")):
            return build_bin
        system_path = shutil.which("pw.x")
        if system_path:
            return os.path.dirname(os.path.abspath(system_path))
        return None

    def verify_existing(self, path: str) -> bool:
        return os.path.exists(os.path.join(path, "pw.x")) or os.path.exists(
            os.path.join(path, "build", "bin", "pw.x")
        )

    def download_and_build(
        self,
        target_dir: str,
        params: dict[str, str],
        env: dict[str, str],
        log_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[bool, BuildResult | None, str]:
        repo_url = "https://gitlab.com/QEF/q-e.git"
        if not self.run_command_streamed(
            ["git", "clone", repo_url, target_dir], ".", "Cloning Q-E v7...", env, log_callback
        ):
            return False, None, "Clone failed."

        build_dir = os.path.join(target_dir, "build")
        os.makedirs(build_dir, exist_ok=True)

        target_arch = params.get("arch", "cpu")

        if target_arch == "gpu":
            # hpcx-mpi mpif90 wraps nvfortran; cmake detects NVHPC compiler ID
            # through the MPI wrapper, satisfying QE's mandatory NVHPC check.
            cmake_flags = [
                "cmake",
                "..",
                "-DCMAKE_INSTALL_PREFIX=..",
                "-DCMAKE_Fortran_COMPILER=mpif90",
                "-DCMAKE_C_COMPILER=mpicc",
                "-DCMAKE_CXX_COMPILER=mpicxx",
                "-DQE_ENABLE_MPI=ON",
                "-DQE_ENABLE_OPENMP=ON",
                "-DQE_FFTW_VENDOR=Internal",
                "-DQE_GPU=cuda",
                "-DQE_GPU_ARCHS=80",
            ]
        else:
            cmake_flags = [
                "cmake",
                "..",
                "-DCMAKE_INSTALL_PREFIX=..",
                "-DCMAKE_C_COMPILER=mpicc",
                "-DCMAKE_Fortran_COMPILER=mpif90",
                "-DQE_ENABLE_OPENMP=ON",
                "-DQE_ENABLE_MPI=ON",
                "-DQE_FFTW_VENDOR=Internal",
            ]

        if not self.run_command_streamed(
            cmake_flags, build_dir, "Configuring QE with CMake...", env, log_callback
        ):
            return False, None, "CMake configuration failed."

        if not self.run_command_streamed(
            ["make", "-j"], build_dir, "Building QE...", env, log_callback
        ):
            return False, None, "Make build failed."

        bin_dir = os.path.join(target_dir, "bin")
        return (
            True,
            BuildResult(binary_path=bin_dir, metadata={"target_arch": target_arch}),
            "QE v7 built successfully.",
        )
