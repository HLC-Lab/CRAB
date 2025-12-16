import json
import subprocess
import argparse

if __name__ == "__main__":
    BENCHES = ["a2a", "a2a_a2a-cong", "a2a_inc-cong", "agtr", "agtr_a2a-cong", "agtr_inc-cong"]
    pauses = ["0.01","0.0001","0.000001"]
    lengths = ["0.1","0.01","0.001"]
    system_data = {
        "name": "cresco8",
        "partition": "cresco8_cpu",
        "account": "ssheneaadm",
        "path": "/afs/enea.it/fra/user/faltelli/CRAB/wrappers/"
    }

    for bench in BENCHES
        config_file = f"huawei_cresco8_sustained/h_{bench}.json
        print(config_file)

        # --- Update JSON ---
        with open(config_file, "r") as f:
            config = json.load(f)

        config.setdefault("global_options", {})
        config["global_options"]["slurm_partition"] = system_data["partition"]
        config["global_options"]["slurm_account"] = system_data["account"]
        for i in range(8):
            if "agtr" in bench:
                config["applications"][str(i)]["path"] = system_data["path"]+"agtr_comm_only.py"
            elif "a2a" in bench:
                config["applications"][str(i)]["path"] = system_data["path"]+"a2a_comm_only.py"
            else:
                raise RuntimeError("No valid collective.")

        if "inc" in bench:
            config["applications"][str(8)]["path"] = system_data["path"]+"noise_incast.py"
        elif "a2a" in bench:
            config["applications"][str(8)]["path"] = system_data["path"]+"noise_a2a.py"
        else:
            raise RuntimeError("No valid noise.")

        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)

    for bp in pauses:
        for bl in lengths:
            for bench in BENCHES:
                if "cong" not in bench:
                    continue
                else:
                    config_file = f"huawei_cresco8_bursty/h_{bench}_{bp}_{bl}.json"
                print(config_file)

                # --- Update JSON ---
                with open(config_file, "r") as f:
                    config = json.load(f)

                config.setdefault("global_options", {})
                config["global_options"]["slurm_partition"] = system_data["partition"]
                config["global_options"]["slurm_account"] = system_data["account"]
                for i in range(8):
                    if "agtr" in bench:
                        config["applications"][str(i)]["path"] = system_data["path"]+"agtr_comm_only.py"
                    elif "a2a" in bench:
                        config["applications"][str(i)]["path"] = system_data["path"]+"a2a_comm_only.py"
                    else:
                        raise RuntimeError("No valid collective.")

                if "inc" in bench:
                    config["applications"][str(8)]["path"] = system_data["path"]+"bursty_noise_incast.py"
                elif "a2a" in bench:
                    config["applications"][str(8)]["path"] = system_data["path"]+"bursty_noise_a2a.py"
                else:
                    raise RuntimeError("No valid noise.")

                with open(config_file, "w") as f:
                    json.dump(config, f, indent=4)
