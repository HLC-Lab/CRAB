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

    def get_launcher_override(self) -> str:
        # Force the framework to use mpirun, bypassing the Slurm defaults
        return "mpirun"

    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None
            
        base_path = receipt.get("binary_path", "")
        install_type = receipt.get("type", "source")
        
        # If loaded via module, the binary is already in the system PATH.
        if install_type == "module":
            return base_path
            
        # If compiled from source, construct the physical absolute path.
        if base_path.endswith("bin"):
            base_path = os.path.dirname(base_path)
        return os.path.join(base_path, "build", "bin", "pw.x")

def run_app(self):
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)
        
        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # 1. Use the explicitly injected run directory
        run_dir = getattr(self, 'run_dir', os.getcwd())
        sandbox_dir = os.path.join(run_dir, "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        # 2. Preserve provenance (copy original input to run dir)
        shutil.copy(input_file, os.path.join(run_dir, "original_input.in"))
        
        # 3. Handle Pseudopotentials locally inside the experiment run
        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)
        
        # 4. Modify .in file to force Absolute Paths for the sandbox
        modified_in = os.path.join(run_dir, "modified_input.in")
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

        content = str(self.stdout)
        
        # Dump a physical .out file into the isolated run directory
        run_dir = getattr(self, 'run_dir', os.getcwd())
        with open(os.path.join(run_dir, "pw.out"), "w") as f_out:
            f_out.write(content)
        
        # Explicitly lock onto the final master summary block line: "PWSCF        :      0.43s CPU      0.47s WALL"
        match_pwscf = re.search(r"PWSCF\s*:\s*[\d.]+\s*s\s*CPU\s*([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match_pwscf:
            return [[float(match_pwscf.group(1))]]

        # Fallback inline layout check
        match_inline = re.search(r"([\d.]+)\s*s\s*WALL", content, re.IGNORECASE)
        if match_inline:
            return [[float(match_inline.group(1))]]
            
        return [[0.0]]
