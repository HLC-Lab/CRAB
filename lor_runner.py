#!/usr/bin/env python3
import json
import subprocess
import argparse


def BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data):
    prev_job = None
    print("Running bursty benchmark on system:", SYSTEM)
    for bp in pauses:
        for bl in lengths:
            for bench in BENCHES:
                config_file = f"huawei_bursty/h_{bench}_{bp}_{bl}.json"
                print(config_file)

                # --- Update JSON ---
                with open(config_file, "r") as f:
                    config = json.load(f)

                config.setdefault("global_options", {})
                config["global_options"]["prevjob"] = prev_job if prev_job else "-1"
                config["global_options"]["prevjob"] = nodes
                config["global_options"]["slurm_partition"] = system["partition"]
                config["global_options"]["slurm_account"] = system["account"]

                with open(config_file, "w") as f:
                    json.dump(config, f, indent=4)

                # --- Submit job ---
                cmd = ["python", "cli.py", "--preset", system_data["name"], "--config", config_file]
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


def SustainedBenchmark(BENCHES, nodes, system_data):
    prev_job = None
    print("Running sustained benchamrk on system:", system_data["name"])
    for bench in BENCHES:
        config_file = f"huawei_sustained/h_{bench}.json"
        print(config_file)

        # --- Update JSON ---
        with open(config_file, "r") as f:
            config = json.load(f)

        config.setdefault("global_options", {})
        config["global_options"]["prevjob"] = prev_job if prev_job else "-1"
        config["global_options"]["prevjob"] = nodes
        config["global_options"]["slurm_partition"] = system["partition"]
        config["global_options"]["slurm_account"] = system["account"]

        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)

        # --- Submit job ---
        cmd = ["python", "cli.py", "--preset", system_data["name"], "--config", config_file]
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
    parser.add_argument("--type", required=True, help="Benchmark type: 'sustained' for standard, 'bursty' for bursty, 'all' for both.")
    args = parser.parse_args()

    TYPE = args.type  
    BENCHES = ["a2a", "a2a_a2a-cong", "a2a_incast-cong", "agtr", "agtr_a2a-cong", "agtr_incast-cong"]
    pauses = ["0.01","0.0001","0.000001"]
    lengths = ["0.1","0.01","0.001"]
    node_list = [8, 16, 32, 64, 128]
    system_data = {
        "name": "cresco8",
        "partition": "cresco8_cpu",
        "account": "enea"
    }

    cmd = ["rm", "-rf", "data"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr

    if(TYPE == "sustained"):
        for nodes in node_list:
            SustainedBenchmark(BENCHES, nodes, system_data)
    elif(TYPE == "bursty"):
        for nodes in node_list:
            BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data)
    elif(TYPE == "all"):
        for nodes in node_list:
            SustainedBenchmark(BENCHES, nodes, system_data)
            BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data)


if __name__ == "__main__":
    main()
