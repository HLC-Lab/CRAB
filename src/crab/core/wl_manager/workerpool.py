import os

# ---------------------------------------------------------------------------
# UNPORTED / LEGACY workload manager.
# This module has NOT been ported to the current run_job() interface used by
# core/process/manager.py, which passes a `launcher` argument (see slurm.py for
# the ported reference implementation). Until it is ported it raises
# NotImplementedError if invoked. See fix_plan.md.
# ---------------------------------------------------------------------------


class wl_manager:
    # Generates a script that can be used to run all the benchmarks specified in the schedule.
    def write_script(self, runner_args, schedules, nams, name, splits, node_file, ppn):
        script = open(name, "w+")
        script.write("#!/bin/bash\nfor schedule in " + " ".join(schedules) + "\ndo\n")
        script.write("\tfor nam in " + " ".join(nams) + "\n\tdo\n")
        script.write("\t\tfor split in " + " ".join(splits) + "\n\t\tdo\n")
        script.write(
            '\t\tpython3 runner.py "$schedule" '
            + node_file
            + ' -am "$nam" -as "$split"'
            + runner_args
            + " -p "
            + str(ppn)
        )
        script.write("\n\t\tdone\n\tdone\ndone")
        script.close()

    # Returns a string that can be used to run command 'cmd'
    # on the nodes in 'node_list' with 'ppn' processes per node,
    # executing "pre_commands" before cmd
    def run_job(
        self,
        node_list: list[str],
        ppn: int,
        cmd: str,
        pre_commands: list[str] | None = None,
        data_path: str = None,
        launcher: str | None = None,
    ) -> str:
        raise NotImplementedError(
            "The 'workerpool' workload manager has not been ported to the current "
            "run_job(launcher=...) interface (see fix_plan.md)."
        )

        # print("[INFO] Workload manager is launching with WorkerPool", flush=True)

        num_nodes = len(node_list)
        node_list_string = ",".join(node_list)
        node_list_arg = "--nodelist " + node_list_string
        final_string = ""

        # print(f"[INFO]: {cmd.split('/')}", flush=True)

        #! THE WORKERPOOL HANDLES THE SRUN
        if cmd.split(" ")[0].split("/")[-1] == "workerpool_scheduler.py":
            workerpool_string = (
                "python "
                + cmd
                + " "
                + "--output-dir "
                + data_path
                + "/workerpool_output "
                + node_list_arg
            )
            final_string = workerpool_string

        else:  #! BASIC SLURM APPLICATION, IT REQUIRES SRUN
            # --- LOGICA DEL WRAPPER ---
            if pre_commands and len(pre_commands) > 0:
                # 1. Silenziamo ogni comando preliminare
                #    Aggiungiamo ' >/dev/null 2>&1' a ciascun comando dell'header
                #    Questo butta via sia stdout che stderr dei moduli.
                #    Se vuoi vedere gli errori ma non l'output, usa solo ' >/dev/null'
                silenced_pre_commands = [f"{c} >/dev/null 2>&1" for c in pre_commands]

                # 2. Uniamo i comandi silenziati e il comando finale con '&&'
                #    Nota: cmd (l'app) NON viene silenziato, perché ci serve il suo output!
                full_sequence = " && ".join(silenced_pre_commands + [cmd])

                # 3. Escaping delle virgolette singole per sicurezza dentro bash -c '...'
                safe_sequence = full_sequence.replace("'", "'\\''")

                # 4. Avvolgiamo tutto in bash -c
                final_cmd = f"bash -c '{safe_sequence}'"
            else:
                # Nessun pre-comando, esecuzione diretta (Legacy/Simple mode)
                final_cmd = cmd
            # --------------------------

            slurm_string = (
                "srun --export=ALL "
                + node_list_arg
                + " "
                + os.environ.get("CRAB_PINNING_FLAGS", "")
                + " "
                + "-n "
                + str(ppn * num_nodes)
                + " "
                + "-N "
                + str(num_nodes)
                + " "
                + final_cmd  # Usiamo il comando calcolato (wrapped o raw)
            ).strip()

            final_string = slurm_string

        # print("[INFO]: SLURM command is: " + final_string, flush=True)
        return final_string
