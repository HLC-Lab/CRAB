import os
import shutil
from crab.wrappers.base import base

class app(base):
    @property
    def benchmark_id(self) -> str:
        return "quantum_espresso"

    def get_binary_path(self):
        # We access the binary path from the receipt (stored in base)
        receipt = self.get_receipt()
        if not receipt:
            return None
        return os.path.join(receipt.get("binary_path", ""), "pw.x")

    def run_app(self):
        # 1. Access configuration passed from the experiment JSON
        # Assuming the Runner/Manager injects these into the app instance
        input_file = getattr(self, 'input_file', None)
        pseudo_dir_source = getattr(self, 'pseudo_dir', None)
        
        if not input_file or not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # 2. Setup Sandbox
        sandbox_dir = os.path.join(os.getcwd(), "scratch")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        # 3. Handle Pseudopotentials
        # Copy the contents of the user's pseudo_dir into our scratch/pseudo
        target_pseudo_dir = os.path.join(sandbox_dir, "pseudo")
        if pseudo_dir_source and os.path.exists(pseudo_dir_source):
            if not os.path.exists(target_pseudo_dir):
                shutil.copytree(pseudo_dir_source, target_pseudo_dir)
        
        # 4. Modify .in file for Sandbox
        # We create 'modified_input.in' in the current experiment dir
        modified_in = "modified_input.in"
        with open(input_file, 'r') as f_in, open(modified_in, 'w') as f_out:
            for line in f_in:
                # Redirect QE output directory and pseudo_dir
                if "outdir" in line.lower():
                    f_out.write(f"    outdir = '{sandbox_dir}/'\n")
                elif "pseudo_dir" in line.lower():
                    f_out.write(f"    pseudo_dir = '{target_pseudo_dir}/'\n")
                else:
                    f_out.write(line)
        
        # 5. Return command string
        binary = self.get_binary_path()
        return f"{binary} -in {modified_in}"

    def read_data(self):
        # Logic to parse the final time from the output file
        # We will add this once we have the output file ready
        return [[0.0]]
