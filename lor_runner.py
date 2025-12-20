#!/usr/bin/env python3
import json
import subprocess
import argparse


def BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data, prev_job):

    print("Running bursty benchmark on system:", system_data["name"])
    for bp in pauses:
        for bl in lengths:
            for bench in BENCHES:

                sys = system_data["name"]
                config_file = f"huawei_jsons/huawei_{sys}_bursty/h_{bench}_{bp}_{bl}.json"
                print(config_file)

                # --- Update JSON ---
                with open(config_file, "r") as f:
                    config = json.load(f)

                config.setdefault("global_options", {})
                config["global_options"]["prevjob"] = prev_job if prev_job else "-1"
                config["global_options"]["numnodes"] = nodes
                config["global_options"]["slurm_partition"] = system_data["partition"]
                config["global_options"]["slurm_account"] = system_data["account"]

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
    return prev_job


def SustainedBenchmark(BENCHES, nodes, system_data, prev_job):
    
    print("Running sustained benchamrk on system:", system_data["name"])
    for bench in BENCHES:
        sys = system_data["name"]
        config_file = f"huawei_jsons/huawei_{sys}_sustained/h_{bench}.json"
        print(config_file)

        # --- Update JSON ---
        with open(config_file, "r") as f:
            config = json.load(f)

        config.setdefault("global_options", {})
        config["global_options"]["prevjob"] = prev_job if prev_job else "-1"
        config["global_options"]["numnodes"] = nodes
        config["global_options"]["slurm_partition"] = system_data["partition"]
        config["global_options"]["slurm_account"] = system_data["account"]
        for i in range(8):
            if "agtr" in bench:
                config["applications"][str(i)]["path"] = system_data["path"]+"agtr_comm_only.py"
            elif "a2a" in bench:
                config["applications"][str(i)]["path"] = system_data["path"]+"a2a_comm_only.py"
            else:
                raise RuntimeError("No valid collective.")

        if "cong" in bench:
            if "inc" in bench:
                config["applications"][str(8)]["path"] = system_data["path"]+"noise_incast.py"
            elif "a2a" in bench:
                config["applications"][str(8)]["path"] = system_data["path"]+"noise_a2a.py"
            else:
                raise RuntimeError("No valid noise.")
                

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
    return prev_job



# --------------------------------- #
# Entry Point
# --------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Run chained jobs for different benchmarks.")
    parser.add_argument("--type", required=True, help="Benchmark type: 'sustained' for standard, 'bursty' for bursty, 'all' for both.")
    parser.add_argument("--system", required=True, help="leonardo, cresco8 or lumi")
    args = parser.parse_args()

    TYPE = args.type  
    SYS = args.system
    BENCHES = ["a2a_a2a-cong", "a2a_inc-cong", "agtr_a2a-cong", "agtr_inc-cong", "a2a", "agtr"]
    pauses = ["0.01","0.0001","0.000001"]
    lengths = ["0.1","0.01","0.001"]

    node_list = [128] #128,256

    if SYS == "leonardo":
        system_data = {
            "name": "leonardo",
            "partition": "boost_usr_prod",
            "account": "IscrB_SWING",
            "path": "/leonardo/home/userexternal/lpiarull/CRAB/wrappers/"
        }
    elif SYS == "cresco8":
        system_data = {
            "name": "cresco8",
            "partition": "cresco8_cpu",
            "account": "ssheneaadm",
            "path": "/afs/enea.it/fra/user/faltelli/CRAB/wrappers/"
        }
    elif SYS == "lumi":
        system_data = {
            "name": "lumi",
            "partition": "standard-g",
            "account": "project_465001736",
            "path": "/users/pasqualo/CRAB/wrappers/"
        }
    else:
        raise RuntimeError("No configuration for the system: " + SYS)


    prev_job = None
    if(TYPE == "sustained"):
        for nodes in node_list:
            prev_job = SustainedBenchmark(BENCHES, nodes, system_data, prev_job)
    elif(TYPE == "bursty"):
        for nodes in node_list:
            prev_job = BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data, prev_job)
    elif(TYPE == "all"):
        for nodes in node_list:
            prev_job = BurstyBenchmark(BENCHES, nodes, pauses, lengths, system_data, prev_job)
        for nodes in node_list:
            prev_job = SustainedBenchmark(BENCHES, nodes, system_data, prev_job) 




if __name__ == "__main__":

    main()
