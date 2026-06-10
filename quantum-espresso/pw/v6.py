import os
import re
import sys
sys.path.append(os.path.dirname(__file__))
from pw_base import pw_base


class app(pw_base):

    @property
    def benchmark_id(self) -> str:
        return "qe-v6"

    def read_data(self) -> list:
        if not hasattr(self, 'stdout') or not self.stdout:
            return [[0.0]]

        content = str(self.stdout)
        run_dir = getattr(self, 'run_dir', os.getcwd())
        with open(os.path.join(run_dir, "pw.out"), "w") as f_out:
            f_out.write(content)

        match = re.search(r"PWSCF\s*:\s*[\d.]+\s*s\s*CPU\s*([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match:
            return [[float(match.group(1))]]

        match = re.search(r"([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match:
            return [[float(match.group(1))]]

        return [[0.0]]
