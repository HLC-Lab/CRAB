import os
import re
import shutil
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

    def run_app(self):
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)
        
        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # Pin sandbox directory directly inside the active tracking workspace
        sandbox_dir = os.path.join(os.getcwd(), "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        # Explicitly copy original input for benchmark provenance
        shutil.copy(input_file, os.path.join(os.getcwd(), "original_input.in"))
        
        # Handle Pseudopotentials inside tracking context
        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)
        
        # Modify .in file for explicit runtime output directions
        modified_in = os.path.join(os.getcwd(), "modified_input.in")
        with open(input_file, 'r') as f_in, open(modified_in, 'w') as f_out:
            for line in f_in:
                if "outdir" in line.lower():
                    f_out.write(f"    outdir = '{sandbox_dir}/'\n")
                elif "pseudo_dir" in line.lower():
                    f_out.write(f"    pseudo_dir = '{target_pseudo_dir}/'\n")
                else:
                    f_out.write(line)
        
        binary = self.get_binary_path()
        return f"{binary} -in {modified_in}"

    def read_data(self) -> list:
        if not hasattr(self, 'stdout') or not self.stdout:
            return [[0.0]]

        if isinstance(self.stdout, bytes):
            content = self.stdout.decode('utf-8', errors='replace')
        else:
            content = str(self.stdout)
        
        # Check standard inline layout: e.g., "  12.75s WALL"
        match_inline = re.search(r"([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match_inline:
            return [[float(match_inline.group(1))]]

        # Fallback layout check: e.g., "Total wall time:     0m 5.12s"
        match_legacy = re.search(r"Total wall time:\s*(?:([\d.]+)h)?\s*(?:([\d.]+)m)?\s*([\d.]+)s", content, re.IGNORECASE)
        if match_legacy:
            hours = float(match_legacy.group(1)) if match_legacy.group(1) else 0.0
            minutes = float(match_legacy.group(2)) if match_legacy.group(2) else 0.0
            seconds = float(match_legacy.group(3))
            
            total_seconds = (hours * 3600) + (minutes * 60) + seconds
            return [[total_seconds]]
            
        return [[0.0]]
