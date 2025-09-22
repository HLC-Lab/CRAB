import subprocess
import numpy as np
import scipy.stats as st
import math
import time
import importlib.util
import pathlib
import sys
import os
import random
import datetime
import pandas
import signal
import shlex
import json
from typing import Callable

class data_container:
    def __init__(self, app_id, conv_goal, label, unit):
        self.app_id = app_id
        self.conv_run = 0
        self.label = label
        self.unit = unit
        self.conv_goal = conv_goal
        self.converged = False
        self.num_samples = []
        self.data = []

    def get_title(self):
        return str(self.app_id)+'_'+self.label+'_'+self.unit

    def md_to_list(self):
        return [self.app_id, self.label, self.unit, self.conv_goal, self.converged, self.conv_run]+self.num_samples


def check_CI(container_list, alpha, beta, converge_all, run):
    # confidence interval parameters: length of CI (two-tailed with 1-alpha confidence) is within beta of reported mean
    for container in container_list:
        # not converged yet and has to converge
        if (not container.converged) and (converge_all or container.conv_goal):
            n = len(container.data)
            if n <= 1:
                continue  # not converged
            mean = np.mean(container.data)
            sem = st.sem(container.data)
            if sem == 0:  # no variance in data
                container.converged = True  # converged
                container.conv_run = run
                continue
            CI_lb, CI_ub = st.t.interval(1-alpha, n-1, loc=mean, scale=sem)
            CI_length = CI_ub-CI_lb
            if CI_length < beta*mean:
                container.converged = True  # converged
                container.conv_run = run

    # check if all convergence flags
    check = True
    for container in container_list:
        if (converge_all or container.conv_goal):
            check = (check and container.converged)
    return check

def run_job(job, wlmanager, ppn):
    if job.num_nodes == 0:
        #TODO: Usa il logger!
        print(f"[WARNING] L'applicazione {job.id_num} ha 0 nodi allocati...")
        return
    cmd_string = wlmanager.run_job(job.node_list, ppn, job.run_app())
    if not cmd_string or cmd_string == "":
        cmd_string = "echo a > /dev/null"
    cmd = shlex.split(cmd_string)
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    job.set_process(process)

def wait_timed(job, time):
    try:
        out, err = job.process.communicate(timeout=time)
        # if didn't time out
        job.set_output(out, err)
        return False
    except subprocess.TimeoutExpired:
        # if timed out
        end_job(job)
        return True


def end_job(job):
    # signal SIGUSR1 if ouput of endless is needed
    # TODO: Restora
    #if job.collect_flag:
    #    job.process.terminate()
    #else:
    #    job.process.kill()
    job.process.kill()
    out, err = job.process.communicate()
    job.set_output(out, err)


def c_allocation(apps, nodes_frame):
    for app in apps:
        nodes_list = list(filter(lambda ele: ele != '',
                          nodes_frame[:][app.id_num].tolist()))
        app.set_nodes(nodes_list)


def l_allocation(apps, node_list, split_absolute):  # linear allocation
    i = 0
    for app, split in zip(apps, split_absolute):
        app.set_nodes(node_list[i:i+split])
        i += split


def i_allocation(apps, node_list, split_absolute):  # interleaved allocation
    num_apps = len(apps)
    ending_condition = [0]*num_apps
    alloc_lists = [[] for _ in range(num_apps)]
    s_index = 0
    n_index = 0
    while split_absolute != ending_condition:
        if split_absolute[s_index] != 0:
            # TODO Far evitare che crashi quando n_index >= len(node_list)
            alloc_lists[s_index] += [node_list[n_index]]
            split_absolute[s_index] -= 1
            n_index += 1
        s_index = (s_index+1) % num_apps
    for app, alloc_list in zip(apps, alloc_lists):
        app.set_nodes(alloc_list)


def get_abs_split(allocation_split, num_apps, num_nodes):
    if allocation_split == 'e':  # equal split among all apps
        split_list = [100/num_apps]*num_apps
    else:
        split_list = [float(x) for x in allocation_split.split(':')]

    if sum(split_list) > 100:
        raise Exception("Splits percentages mustn't add up to more than 100.")
    '''
    if len(split_list) != num_apps:
        raise Exception('Number of applications ('+str(num_apps)+') is not equal to number of splits ('
                        + str(len(split_list))+')')
    '''
    if len(split_list) < num_apps:
        raise Exception('Number of applications ('+str(num_apps)+') is larger than number of splits ('
                    + str(len(split_list))+')')
    
    split_list = split_list[:num_apps]
    print("Split list: " + str(split_list))
    split_absolute = []
    for split in split_list[:-1]:
        split_a = int(math.ceil(num_nodes*split/100))
        split_absolute += [split_a]
        print("Split_a: " + str(split_a))
    if num_apps == 1:
        split_absolute = [int(math.ceil(num_nodes*split_list[0]/100))]
    else:
        # allocate all remaining nodes to the last application
        split_absolute += [num_nodes-sum(split_absolute)]
    print("Splits: " + str(split_absolute))
    return split_absolute


def log_meta_data(out_format, path, data_container_list, num_runs):
    num_samples_index = list(
        map(lambda x: 'Run '+str(x), list(range(1, num_runs+1))))
    dataframe = pandas.DataFrame()
    for i, container in enumerate(data_container_list):
        dataframe = pandas.concat([dataframe, pandas.DataFrame(
            container.md_to_list())], axis=1, ignore_index=True)
    dataframe.index = ['App_id', 'Label', 'Unit', 'Convergence_goal',
                       'Converged', 'Converged_run']+num_samples_index
    if out_format == 'csv':
        file_name = path+'.csv'
        dataframe.to_csv(file_name, index=True)
    elif out_format == 'hdf':
        file_name = path+'.h5'
        dataframe.to_hdf(file_name, key='df',
                         data_columns=data_header_cols, index=False)

def log_data(out_format, data_path_prefix, data_container_list):
    # Raggruppa i container per app_id
    apps_data = {}
    for container in data_container_list:
        if container.app_id not in apps_data:
            apps_data[container.app_id] = []
        apps_data[container.app_id].append(container)

    # Scrivi un file separato per ogni applicazione che ha raccolto dati
    for app_id, containers in apps_data.items():
        data_dict = {c.get_title(): c.data for c in containers}
        
        # Gestisci il problema delle lunghezze diverse *all'interno della stessa app*
        # (non dovrebbe succedere con i microbench, ma è una buona pratica)
        # Riempi le liste più corte con NaN per pareggiare le lunghezze.
        max_len = 0
        if data_dict:
             max_len = max(len(v) for v in data_dict.values())
        
        for k, v in data_dict.items():
            if len(v) < max_len:
                v.extend([None] * (max_len - len(v)))

        dataframe = pandas.DataFrame(data_dict)
        
        # Costruisci un nome di file specifico per questa app
        file_path = f"{data_path_prefix}_app_{app_id}"
        
        if out_format == 'csv':
            file_name = file_path + '.csv'
            dataframe.to_csv(file_name, index=False)
        elif out_format == 'hdf':
            file_name = file_path + '.h5'
            dataframe.to_hdf(file_name, key='df', index=False)
        print(f"Dati per App {app_id} salvati in: {file_name}")

def print_runtime(obj, mode, ro_file):
    if mode == 'stdout':
        print(obj)
    elif mode == 'none':
        return
    elif mode == '+file':
        print(obj)
        print(obj, file=ro_file)
    elif mode == 'file':
        print(obj, file=ro_file)


class Engine:
    def __init__(self, log_callback: Callable[[str], None] = print):
        self.log = log_callback

    def run(self, config: dict, environment: dict):
        """
        Executes the benchmark.
        Temporarily updates the global os.environ for the duration of the run
        to ensure compatibility with wrappers that rely on it.
        """
        # 1. Salva l'ambiente originale per poterlo ripristinare dopo
        original_environ = os.environ.copy()

        try:
            # 2. Aggiorna l'ambiente del processo corrente con le variabili preparate
            os.environ.update(environment)

            pre_start_time = time.time()
            
            # USA I DATI DAI PARAMETRI, NON DA ARGS
            global_options = config.get('global_options', {})
            applications_config = config.get('applications', {})

            # Usa l'environment passato come parametro
            import_path_wlm = "./src/crab/core/wl_manager/" + \
                os.environ["BLINK_WL_MANAGER"] + ".py"
            
            node_file = global_options.get('node_file')
            if not node_file:
                node_file = global_options.get('nodes', 'auto')

            num_nodes_str = global_options.get('numnodes')
            if num_nodes_str is None or not str(num_nodes_str).isdigit():
                raise ValueError("La chiave 'numnodes' nel file JSON e' obbligatoria e deve essere un intero.")
            num_nodes = int(num_nodes_str)

            # --- Parameter extraction from config dictionaries ---
            allocation_mode = global_options.get('allocationmode', 'l')
            allocation_split = global_options.get('allocationsplit', 'e')
            min_runs = int(global_options.get('minruns', 10))
            max_runs = int(global_options.get('maxruns', 1000))
            time_out = float(global_options.get('timeout', 100.0))
            alpha = float(global_options.get('alpha', 0.05))
            beta = float(global_options.get('beta', 0.05))
            ppn = int(global_options.get('ppn', 1))
            converge_all = bool(global_options.get('convergeall', False))
            out_format = global_options.get('outformat', 'csv')
            data_path = global_options.get('datapath', './data')
            extrainfo = global_options.get('extrainfo', '')
            replace_mix_args = global_options.get('replace_mix_args', '')

            random.seed(int(global_options.get('seed', 1)))

            # --- Node file and SLURM auto-detection ---
            if node_file == "auto":
                # The 'auto' node_file feature requires SLURM.
                if "BLINK_WL_MANAGER" not in os.environ or os.environ["BLINK_WL_MANAGER"] != "slurm":
                    raise Exception("'auto' node file can only be used if SLURM is the workload manager.")
                
                node_file_path = "auto_node_file_" + str(os.getpid()) + ".txt"

                # Get a list of available (idle or mixed) nodes from SLURM.
                nodelist = subprocess.check_output(["sinfo", "-h", "-o", "%N", "-t", "idle,mixed"], text=True).strip()

                # Check if any nodes were found.
                if not nodelist:
                    raise Exception("Error: No 'idle' or 'mixed' nodes found on the cluster at this time. Please try again later.")
                
                # Write the hostnames to a temporary file.
                with open(node_file_path, "w") as f:
                    subprocess.call(["scontrol", "show", "hostnames", nodelist], stdout=f)
                node_file = node_file_path # Update node_file to the path of the generated file

            # --- Output Directory and Metadata Logging Setup ---
            # Create header in the main description file if it doesn't exist.
            description_file = os.path.join(data_path, "description.csv")
            if not os.path.isfile(description_file):
                os.makedirs(data_path, exist_ok=True)
                with open(description_file, 'w') as desc_file:
                    desc_file.write('app_mix,system,numnodes,allocation_mode,allocation_split,ppn,out_format,extra,path\n')

            # Create a unique directory for this specific run using a timestamp.
            while True:
                runner_id = (os.environ["BLINK_SYSTEM"] + "/" + datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
                data_directory = os.path.join(data_path, runner_id)
                if not os.path.exists(data_directory):
                    os.makedirs(data_directory)
                    break

            # Append a new entry for this run to the description file.
            with open(description_file, 'a+') as desc_file:
                extra = extrainfo
                desc_line = (
                    f"{os.environ['BLINK_SYSTEM']},{num_nodes},"
                    f"{allocation_mode},{allocation_split},{ppn},{out_format},{extra},{data_directory}\n"
                )
                desc_file.write(desc_line)
                   
            self.log('\nConfig from JSON:')
            self.log(json.dumps(config, indent=4))

            # --- Application and Schedule Initialization ---
            num_apps = 0
            apps = []  # List to hold application instances
            apps_awaited = []  # List of app indices to wait for at the end of each run
            apps_waiting = []  # List of app indices that are forcefully terminated
            schedule = []  # Execution schedule: list of (app_id, action, time_stamp)

            self.log('\nApps:')

            # Iterate over the application configuration to build the schedule.
            # Sorting by key ensures a predictable processing order.
            sorted_apps = sorted(applications_config.items(), key=lambda item: int(item[0]))

            for app_key, app_details in sorted_apps:
                import_path_app = app_details.get("path")

                # Skip if the path is not defined.
                if not import_path_app:
                    continue

                app_args = app_details.get("args", "")
                collect_flag = app_details.get("collect", False)
                start_val = app_details.get("start", "")
                end_val = app_details.get("end", "")

                # Replace argument placeholders if specified.
                if replace_mix_args:
                    for replacement in replace_mix_args.split(","):
                        key, value = replacement.split(":")
                        app_args = app_args.replace(key, value)

                # Schedule the start time for the app.
                start_time = 0.0 if start_val == '' else float(start_val)
                schedule.append((num_apps, 's', start_time))

                # Schedule the termination condition for the app.
                if end_val == '':
                    apps_awaited.append(num_apps)
                    end_cond = 'run until finished'
                elif end_val == 'f':
                    apps_waiting.append(num_apps)
                    end_cond = 'wait until others finished'
                else:
                    end_time = float(end_val)
                    if end_time < start_time:
                        raise Exception(f'Application {num_apps} must be started before it can be killed.')
                    
                    action = 't' if collect_flag else 'k'  # 't' for terminate (SIGTERM), 'k' for kill (SIGKILL)
                    schedule.append((num_apps, action, end_time))
                    end_cond = f'terminate at: {end_time}s'

                # Dynamically import the application's wrapper module.
                app_class_name = pathlib.Path(import_path_app).stem
                self.log(f"Loading wrapper: {app_class_name} from {import_path_app}")
                spec_app = importlib.util.spec_from_file_location(app_class_name, import_path_app)
                mod_app = importlib.util.module_from_spec(spec_app)
                spec_app.loader.exec_module(mod_app)
                apps.append(mod_app.app(num_apps, collect_flag, app_args))

                self.log(f'{num_apps}: with arguments:"{app_args}", collection flag: {collect_flag}, ending condition: {end_cond}.')
                num_apps += 1

            if num_apps == 0:
                raise Exception('Must specify at least one application to run.')

            # Dynamically import the workload manager module.
            wlm_class_name = pathlib.Path(import_path_wlm).stem
            spec_wlm = importlib.util.spec_from_file_location(wlm_class_name, import_path_wlm)
            mod_wlm = importlib.util.module_from_spec(spec_wlm)
            spec_wlm.loader.exec_module(mod_wlm)
            wlmanager = mod_wlm.wl_manager()

            # --- Node Allocation ---
            nodes_frame = pandas.read_csv(node_file, header=None, keep_default_na=False, dtype=str)
            with open(node_file, 'r') as f:
                self.log(f"[DEBUG] Content of node file '{node_file}':\n{f.read()}")

            num_cols = nodes_frame.shape[1]
            if num_nodes % num_cols != 0:
                raise Exception(f'Number of nodes ({num_nodes}) must be a multiple of columns ({num_cols}) in {node_file}.')
            nodes_frame = nodes_frame.head(num_nodes // num_cols)

            if allocation_mode == 'c':  # Custom allocation from file
                if num_cols != num_apps:
                    raise Exception(f'Must specify exactly one column of nodes for every application in {node_file}.')
                c_allocation(apps, nodes_frame)
            else:
                split_absolute = get_abs_split(allocation_split, num_apps, num_nodes)
                node_list = nodes_frame.iloc[:, 0].tolist()

                self.log(f"[DEBUG] Node list before allocation: {node_list}")

                if allocation_mode == 'i':
                    i_allocation(apps, node_list, split_absolute)
                else:  # 'l' (linear), 'r' (random), or '+r' (random reshuffle)
                    if allocation_mode in ('r', '+r'):
                        random.shuffle(node_list)
                    l_allocation(apps, node_list, split_absolute)

            self.log('\nNode allocation:')
            self.log(f'Processes per node (ppn): {ppn}')
            for app in apps:
                self.log(f'{app.id_num} on {app.num_nodes} nodes:')
                self.log(str(np.array(app.node_list)))
                with open(os.path.join(data_directory, f"hostnames_{app.id_num}"), "w") as f:
                    f.write("\n".join(map(str, app.node_list)))

            # On specific systems like Alps/LUMI, get node location info.
            if os.environ["BLINK_SYSTEM"] in ("lumi", "alps"):
                with open(os.path.join(data_directory, "xname"), "w") as f:
                    subprocess.call(["srun", "cat", "/etc/cray/xname"], stdout=f)
                    
            # Sort schedule chronologically.
            self.log('\nSchedule:')
            schedule.sort(key=lambda t: t[2])
            for app_id, action_type, action_time in schedule:
                action_str = {'s': 'start', 'k': 'kill', 't': 'terminate'}.get(action_type, 'unknown')
                self.log(f'{action_time}s: {action_str} {app_id}')

            # Prepare data containers for metrics collection.
            data_container_list = []
            for app in apps:
                if app.collect_flag:
                    for i in range(len(app.metadata)):
                        data_container_list.append(
                            data_container(app.id_num, app.metadata[i]["conv"], app.metadata[i]["name"], app.metadata[i]["unit"])
                        )

            # --- Main Execution Loop ---
            self.log('\nPreparing took '+str(round(time.time() - pre_start_time, 5))+'s.')
            self.log('\nRunning...')

            runs = 0
            converged = False
            start_time = time.time()
            timeout_occured = False
            while True:
                exec_time = time.time() - start_time
                
                # Termination conditions
                if runs >= max_runs:
                    self.log(f'Completed maximum number of runs, terminated after {runs} runs taking {round(exec_time, 2)} seconds.')
                    break
                if runs >= min_runs and converged:
                    self.log(f'Completed after reaching confidence interval, terminated after {runs} runs taking {round(exec_time, 2)} seconds.')
                    break
                if exec_time >= time_out:
                    self.log(f'Completed after timeout, terminated after {runs} runs taking {round(exec_time, 2)} seconds.')
                    break
                if (schedule and (time_out - (time.time() - start_time) < schedule[-1][2])):
                    self.log(f'Completed due to insufficient time for next run, terminated after {runs} runs taking {round(exec_time, 2)} seconds.')
                    break

                # Execute schedule for the current run
                self.log(f' Run {runs+1}:')
                run_start_time = time.time()

                if allocation_mode == '+r':
                    random.shuffle(node_list)
                    l_allocation(apps, node_list, split_absolute)
                    self.log('  Reshuffled node allocation.')

                current_time = 0
                for app_id, action_type, action_time in schedule:
                    time.sleep(action_time - current_time)
                    if action_type == 's':
                        self.log(f'  About to start {app_id} at {action_time}s.')
                        run_job(apps[app_id], wlmanager, ppn)
                        self.log(f'    {round(time.time()-run_start_time, 5)}s: started {app_id}')
                    else: # 'k' or 't'
                        end_job(apps[app_id])
                        action_str = 'killed' if action_type == 'k' else 'terminated'
                        self.log(f'    {round(time.time()-run_start_time, 5)}s: {action_str} {app_id}')
                    current_time = action_time

                # Wait for all apps that are supposed to finish on their own.
                timeout_occured = False
                for app_ind in apps_awaited:
                    remaining_time = time_out - (time.time() - start_time)
                    if wait_timed(apps[app_ind], remaining_time):
                        timeout_occured = True
                        self.log(f'    {round(time.time()-run_start_time, 5)}s: timed out awaiting {app_ind}')
                    else:
                        self.log(f'    {round(time.time()-run_start_time, 5)}s: finished awaiting {app_ind}')

                # Forcefully terminate all background 'aggressor' apps.
                for app_ind in apps_waiting:
                    end_job(apps[app_ind])
                    action_str = 'terminated' if apps[app_ind].collect_flag else 'killed'
                    self.log(f'    {round(time.time()-run_start_time, 5)}s: {action_str} waiting app {app_ind}')

                if timeout_occured:
                    self.log('  No data collection due to timeout.')
                    self.log(f'Completed after timeout, terminated after {runs} runs taking {round(time.time()-start_time, 2)} seconds.')
                    break

                runs += 1

                # Check every app for errors.
                for app in apps:
                    if hasattr(app, 'process') and app.process.returncode is not None and app.process.returncode > 0:
                        self.log(f'  {round(time.time()-run_start_time, 5)}s: encountered an exception in {app.id_num} (exit code: {app.process.returncode})')
                        if app.stderr:
                            self.log("--- STDERR ---")
                            self.log(app.stderr)
                            self.log("--------------")
                        raise Exception(f"Application {app.id_num} threw an exception.")

                # Collect data from successful runs.
                data_collection_start_time = time.time()
                container_idx = 0
                for app in apps:
                    if app.collect_flag and hasattr(app, 'process') and app.process.returncode is not None and app.process.returncode <= 0:
                        data_list_of_list = app.read_data()
                        for data_list in data_list_of_list:
                            if container_idx < len(data_container_list):
                                data_container_list[container_idx].data.extend(data_list)
                                data_container_list[container_idx].num_samples.append(len(data_list))
                            container_idx += 1
                self.log('  Data collection took '+str(round(time.time() - data_collection_start_time, 5))+'s.')

                # Check for convergence.
                if runs >= min_runs:
                    conv_check_start_time = time.time()
                    converged = check_CI(data_container_list, alpha, beta, converge_all, runs)
                    self.log('  Convergence check took '+str(round(time.time() - conv_check_start_time, 5))+'s.')

            # --- Final Data Logging ---
            log_start_time = time.time()
            if data_container_list:
                log_data(out_format, os.path.join(data_directory, 'data'), data_container_list)
                log_meta_data(out_format, os.path.join(data_directory, 'metadata'), data_container_list, runs)
            self.log('Writing data & meta-data took ' + str(round(time.time() - log_start_time, 5))+'s.')

            self.log('Overall took '+str(round(time.time() - pre_start_time, 5))+'s.')

            # Cleanup temporary node file if it was created
            if 'auto_node_file_' in node_file and os.path.exists(node_file):
                # TODO: capire perche' elimina il file dei nodi
                # os.remove(node_file)
                pass

        finally:
            # 4. Ripristina l'ambiente originale, qualunque cosa accada (anche in caso di errore)
            # Questo è FONDAMENTALE per non "inquinare" l'ambiente del processo
            # principale.
            os.environ.clear()
            os.environ.update(original_environ)
