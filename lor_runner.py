#!/usr/bin/env python3
import json
import subprocess
import argparse


def BurstyBenchmark(SYSTEM, BENCHES, pauses, lengths):
    prev_job = None
    print("Running bursty benchmark on system:", SYSTEM)
    for bp in pauses:
        for bl in lengths:
            for bench in BENCHES:
                config_file = f"huawei_{SYSTEM}_bursty_128B/h_{bench}_{bp}_{bl}.json"
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


def SustainedBenchmark(SYSTEM, BENCHES):
    prev_job = None
    print("Running sustained benchamrk on system:", SYSTEM)
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



# --------------------------------- #
# Entry Point
# --------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Run chained jobs for different benchmarks.")
    parser.add_argument("--system", required=True, help="System preset name (previously exported as SYSTEM).")
    parser.add_argument("--type", required=True, help="Benchmark type: 's' for standard, 'b' for bursty.")
    args = parser.parse_args()

    pauses = ["0.01","0.0001","0.000001"]
    lengths = ["0.1","0.01","0.001"]

    SYSTEM = args.system
    TYPE = args.type  # or b Currently only 'bursty' type is supported
    BENCHES = ["a2a", "a2a_a2a-cong", "a2a_incast-cong"] #["agtr", "agtr_cong"]

    cmd = ["rm", "-rf", "data"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr

    if(TYPE == "s"):
        SustainedBenchmark(SYSTEM, BENCHES)
    elif(TYPE == "b"):
        BurstyBenchmark(SYSTEM, BENCHES, pauses, lengths)


if __name__ == "__main__":
    main()
