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
        # This tells the core runner what metrics we are collecting
        return [
            {
                "name": "wall_time",
                "unit": "seconds",
                "conv": 1.0  # Direct multiplier conversion
            }
        ]

    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None
        return os.path.join(receipt.get("binary_path", ""), "pw.x")

    def run_app(self):
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)
        
        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # Setup Sandbox
        sandbox_dir = os.path.join(os.getcwd(), "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        # Handle Pseudopotentials
        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)
        
        # Modify .in file for Sandbox redirection
        modified_in = "modified_input.in"
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
        # self.stdout contains the raw bytes of the output string after completion
        if not hasattr(self, 'stdout') or not self.stdout:
            return [[0.0]]

        content = self.stdout.decode('utf-8', errors='replace')
        
        # Look for the characteristic QE ending line: e.g., "PWSCF        :     0m 4.50s CPU     0m 5.21s WALL"
        # Or alternative: "Total wall time:     0m 5.21s"
        wall_match = re.search(r"(?:WALL|Total wall time:)\s*(?:([\d.]+)h)?\s*(?:([\d.]+)m)?\s*([\d.]+)s", content, re.IGNORECASE)
        
        if wall_match:
            hours = float(wall_match.group(1)) if wall_match.group(1) else 0.0
            minutes = float(wall_match.group(2)) if wall_match.group(2) else 0.0
            seconds = float(wall_match.group(3))
            
            total_seconds = (hours * 3600) + (minutes * 60) + seconds
            return [[total_seconds]]
            
        return [[0.0]]
