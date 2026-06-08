import os
import shutil
from abc import abstractmethod
from crab.wrappers.base import base


class ph_base(base):
    """Base class for all Quantum ESPRESSO PH (phonon) versions."""

    @property
    @abstractmethod
    def benchmark_id(self) -> str:
        pass

    @property
    def metadata(self) -> list:
        return [{"name": "wall_time", "unit": "seconds", "conv": 1.0}]

    def get_launcher_override(self) -> str:
        return "mpirun"

    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None

        base_path = receipt.get("binary_path", "")
        install_type = receipt.get("type", "source")

        if install_type == "module":
            return base_path

        if install_type == "binary":
            return os.path.join(base_path, "ph.x")

        # source: binary_path = target_dir/bin (CMake install prefix stub)
        if base_path.endswith("bin"):
            base_path = os.path.dirname(base_path)
        return os.path.join(base_path, "build", "bin", "ph.x")

    def run_app(self):
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)

        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        run_dir = getattr(self, 'run_dir', os.getcwd())
        sandbox_dir = os.path.join(run_dir, "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)

        shutil.copy(input_file, os.path.join(run_dir, "original_input.in"))

        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)

        # ph.x uses &INPUTPH namelist; outdir must match the pw.x scratch dir
        modified_in = os.path.join(run_dir, "modified_input.in")
        with open(input_file, 'r') as f_in, open(modified_in, 'w') as f_out:
            for line in f_in:
                if "outdir" in line.lower():
                    f_out.write(f"    outdir = '{sandbox_dir}/'\n")
                elif "pseudo_dir" in line.lower():
                    f_out.write(f"    pseudo_dir = '{target_pseudo_dir}/'\n")
                else:
                    f_out.write(line)

        binary = self.get_binary_path()
        return f"{binary} < {modified_in}"
