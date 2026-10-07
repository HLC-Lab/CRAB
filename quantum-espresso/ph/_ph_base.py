import os
import re
import shutil
import sys
from abc import abstractmethod
from crab.wrappers.base import base

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _qe_binary import ph_next_to, pw_from_receipt  # noqa: E402
from _qe_output import read_wall_time  # noqa: E402


class ph_base(base):
    """Base class for all Quantum ESPRESSO PH (phonon) versions."""

    @property
    @abstractmethod
    def benchmark_id(self) -> str:
        pass

    @property
    def metadata(self) -> list:
        return [{"name": "wall_time", "unit": "seconds", "conv": 1.0}]

    def read_data(self) -> list:
        content = str(self.stdout)
        run_dir = getattr(self, 'run_dir', os.getcwd())
        with open(os.path.join(run_dir, "ph.out"), "w") as f_out:
            f_out.write(content)

        return read_wall_time(content, "PHONON")

    def get_binary_path(self):
        # The receipt is shared with pw and points at pw.x; ph.x sits next to it.
        return ph_next_to(pw_from_receipt(self.get_receipt()), self.benchmark_id)

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
                if re.match(r'^\s*outdir\s*=', line, re.IGNORECASE):
                    f_out.write(f"    outdir = '{sandbox_dir}/'\n")
                elif re.match(r'^\s*pseudo_dir\s*=', line, re.IGNORECASE):
                    f_out.write(f"    pseudo_dir = '{target_pseudo_dir}/'\n")
                else:
                    f_out.write(line)

        # The binary the engine's pre-flight checked: the config's 'binary', else the receipt.
        binary = self.resolve_binary()
        return f"{binary} < {modified_in}"
