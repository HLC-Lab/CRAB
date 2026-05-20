import os
import re
import shutil
import inspect
from crab.wrappers.base import base

class app(base):
    @property
    def benchmark_id(self) -> str:
        return "quantum_espresso"

    @property
    def metadata(self) -> list:
        return [
            {
                "name": "wall_time",
                "unit": "seconds",
                "conv": 1.0  
            }
        ]

    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None
        base_path = receipt.get("binary_path", "")
        if base_path.endswith("bin"):
            base_path = os.path.dirname(base_path)
        return os.path.join(base_path, "build", "bin", "pw.x")

    def _get_experiment_dir(self):
        """Dynamically extracts the CRAB experiment output directory from the engine stack."""
        frame = inspect.currentframe()
        try:
            while frame:
                # CRAB's runner.execute() explicitly uses the variable 'output_dir'
                if 'output_dir' in frame.f_locals and isinstance(frame.f_locals['output_dir'], str):
                    return frame.f_locals['output_dir']
                frame = frame.f_back
        finally:
            del frame
        return os.getcwd()

    def run_app(self):
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)
        
        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # 1. Anchor everything to the actual CRAB experiment directory
        exp_dir = self._get_experiment_dir()
        sandbox_dir = os.path.join(exp_dir, "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        # 2. Preserve provenance (copy original input to experiment dir)
        shutil.copy(input_file, os.path.join(exp_dir, "original_input.in"))
        
        # 3. Handle Pseudopotentials locally inside the experiment
        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)
        
        # 4. Modify .in file to force Absolute Paths for the sandbox
        modified_in = os.path.join(exp_dir, "modified_input.in")
        with open(input_file, 'r') as f_in, open(modified_in, 'w') as f_out:
            for line in f_in:
                if "outdir" in line.lower():
                    f_out.write(f"    outdir = '{sandbox_dir}/'\n")
                elif "pseudo_dir" in line.lower():
                    f_out.write(f"    pseudo_dir = '{target_pseudo_dir}/'\n")
                else:
                    f_out.write(line)
        
        # 5. Return execution string using explicit absolute paths
        binary = self.get_binary_path()
        return f"{binary} -in {modified_in}"

    def read_data(self) -> list:
        if not hasattr(self, 'stdout') or not self.stdout:
            return [[0.0]]

        # base.py already decodes stdout to a string
        content = str(self.stdout)
        
        # Dump a physical .out file into the experiment directory for user review
        exp_dir = self._get_experiment_dir()
        with open(os.path.join(exp_dir, "pw.out"), "w") as f_out:
            f_out.write(content)
        
        # 1. Match inline format: e.g., "  11.98s CPU     12.75s WALL"
        match_inline = re.search(r"([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match_inline:
            return [[float(match_inline.group(1))]]

        # 2. Legacy format fallback: e.g., "Total wall time:     0m 5.12s"
        match_legacy = re.search(r"Total wall time:\s*(?:([\d.]+)h)?\s*(?:([\d.]+)m)?\s*([\d.]+)s", content, re.IGNORECASE)
        if match_legacy:
            hours = float(match_legacy.group(1)) if match_legacy.group(1) else 0.0
            minutes = float(match_legacy.group(2)) if match_legacy.group(2) else 0.0
            seconds = float(match_legacy.group(3))
            return [[(hours * 3600) + (minutes * 60) + seconds]]
            
        return [[0.0]]
