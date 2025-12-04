#!/usr/bin/env python3
import json
import subprocess
import argparse

def main():
    parser = argparse.ArgumentParser(description="Run chained jobs for different benchmarks.")
    parser.add_argument("--system", required=True, help="System preset name (previously exported as SYSTEM).")
    args = parser.parse_args()

    SYSTEM = args.system
    BENCHES = ["a2a_cong", "a2a"]
    prev_job = None

    for bench in BENCHES:
        config_file = f"huawei_{SYSTEM}/h_{bench}.json"
        print(config_file)

        # --- Update JSON ---
        with open(config_file, "r") as f:
            config = json.load(f)

        config.setdefault("global_options", {})
        config["global_options"]["prevjob"] = prev_job if prev_job else "-1"

        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)

        # --- Submit job ---
        cmd = ["python", "cli.py", "--preset", SYSTEM, "--config", config_file]
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout + result.stderr

        # Extract job ID
        jobid = None
        for line in output.splitlines():
            if "Submitted batch job" in line:
                jobid = line.split()[-1]
                break

        if not jobid:
            raise RuntimeError("Could not extract job ID from output:\n" + output)

        prev_job = jobid
        print(f"Extracted job ID: {prev_job}")

if __name__ == "__main__":
    main()
