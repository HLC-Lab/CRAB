import os
import shlex
class wl_manager:
    # Generates a script that can be used to run all the benchmarks specified in the schedule.
    def write_script(self, runner_args, schedules, nams, name, splits, node_file, ppn):
        script=open(name,'w+')
        script.write('#!/bin/bash\nfor schedule in '+' '.join(schedules)+'\ndo\n')
        script.write('\tfor nam in '+' '.join(nams)+'\n\tdo\n')
        script.write('\t\tfor split in '+' '.join(splits)+'\n\t\tdo\n')
        script.write('\t\tpython3 runner.py "$schedule" '+node_file+' -am "$nam" -as "$split"'+runner_args+' -p '+str(ppn))
        script.write('\n\t\tdone\n\tdone\ndone')
        script.close()

    # Returns a string that can be used to run command 'cmd'
    # on the nodes in 'node_list' with 'ppn' processes per node.
    def run_job(self, node_list, ppn, cmd):
        print("[DEBUG]: Node List is: " + str(node_list))

        num_nodes = len(node_list)
        node_list_string = ','.join(node_list)
        node_list_arg = '--nodelist ' + node_list_string

        # Inizializza le opzioni aggiuntive per SLURM
        slurm_extra_opts = ""

        # Controlla se siamo su Leonardo e, in caso affermativo, aggiungi la partizione
        if os.environ.get("CRAB_SYSTEM") == "leonardo":
            # I nodi 'lrdn' sono nella partizione booster.
            # I nodi 'viz' sono in un'altra, ma per questi test usiamo la booster.
            slurm_extra_opts = "--partition=boost_usr_prod"
            print(f"[DEBUG]: Detected CRAB_SYSTEM=leonardo. Adding SLURM option: {slurm_extra_opts}")

        wrapped_cmd = f"""bash -c '
        module purge >&2
        module load openmpi >&2
        module list >&2
        {cmd}
        '"""
        wrapped_cmd = cmd

        slurm_string = (
            'srun --export=ALL ' +
            slurm_extra_opts + ' ' + # Aggiungiamo qui le opzioni extra
            node_list_arg + ' ' +
            os.environ.get("CRAB_PINNING_FLAGS", "") + ' ' + # Usiamo .get() per sicurezza
            '-n ' + str(ppn * num_nodes) + ' ' +
            '-N ' + str(num_nodes) + ' ' +
            wrapped_cmd
        ).strip() # .strip() rimuove spazi extra all'inizio o alla fine

        print("[DEBUG]: SLURM command is: " + slurm_string)
        return slurm_string
