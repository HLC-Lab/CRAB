#!/usr/bin/env python3
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
import argparse
import datetime
import pandas
import signal
import shlex
import json # Aggiunto import per gestire JSON


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
        print(f"[WARNING] L'applicazione {job.id_num} ha 0 nodi allocati e non verrà eseguita.")
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

# def log_data(out_format, path, data_container_list):
#     # create header for data
#     data_dict = {container.get_title(): container.data for container in data_container_list}
#     dataframe = pandas.DataFrame(data_dict)
#
#     if out_format == 'csv':
#         file_name = path+'.csv'
#         dataframe.to_csv(file_name, index=False)
#     elif out_format == 'hdf':
#         file_name = path+'.h5'
#         dataframe.to_hdf(file_name, key='df',
#                          data_columns=data_header_cols, index=False)

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


def main():
    pre_start_time = time.time()
    
    # --- INIZIO BLOCCO MODIFICATO ---
    # Semplificato argparse per accettare un singolo file di configurazione JSON
    parser = argparse.ArgumentParser(description='Runner of framework from a JSON config file.')
    parser.add_argument('config_file', help='Path to the JSON configuration file.')
    args = parser.parse_args()

    # Carica il file di configurazione JSON
    with open(args.config_file, 'r') as f:
        config = json.load(f)

    global_options = config.get('global_options', {})
    applications_config = config.get('applications', {})

    # argument namespace to variables, leggendo da JSON e usando i default originali
    import_path_wlm = "./app/core/wl_manager/" + \
        os.environ["BLINK_WL_MANAGER"] + ".py"
    
    # Se 'node_file' non e' specificato o e' vuoto, usa 'nodes', altrimenti usa 'node_file'
    node_file = global_options.get('node_file')
    if not node_file:
        node_file = global_options.get('nodes', 'auto')

    # numnodes è obbligatorio, quindi non forniamo un default
    num_nodes_str = global_options.get('numnodes')
    if num_nodes_str is None or not str(num_nodes_str).isdigit():
        raise ValueError("La chiave 'numnodes' nel file JSON e' obbligatoria e deve essere un intero.")
    num_nodes = int(num_nodes_str)

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
    ro_mode = global_options.get('runtimeout', 'stdout')
    data_path = global_options.get('datapath', './data')
    extrainfo = global_options.get('extrainfo', '')
    replace_mix_args = global_options.get('replace_mix_args', '')
    
    random.seed(int(global_options.get('seed', 1)))
    # --- FINE BLOCCO MODIFICATO ---

    if node_file == "auto":
        if not "BLINK_WL_MANAGER" in os.environ or os.environ["BLINK_WL_MANAGER"] != "slurm":
            raise Exception("auto node file can only be used if slurm is used as workload manager.")
        node_file = "auto_node_file_" + str(os.getpid()) + ".txt"

        nodelist = subprocess.check_output(["sinfo", "-h", "-o", "%N", "-t", "idle,mixed"], text=True).strip()
        subprocess.call(["scontrol", "show", "hostnames", nodelist], stdout=open(node_file, "w"))

        # Checks if nodelist is empty
        if not nodelist:
            raise Exception("Errore: Nessun nodo 'idle' o 'mixed' disponibile trovato sul cluster in questo momento. Riprova più tardi.")
    
    # Create header in description.csv
    if not os.path.isfile(data_path + "/description.csv"):
        with open(data_path + '/description.csv', 'w') as desc_file:
            desc_file.write('app_mix,system,numnodes,allocation_mode,allocation_split,ppn,out_format,extra,path\n')        
    
    # runner_id is current time
    while True:
        runner_id = (os.environ["BLINK_SYSTEM"] + "/" + datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S'))
        data_directory = data_path + '/' + runner_id
        if not os.path.exists(data_directory):
            os.makedirs(data_directory)
            break
    
    # Append info to description.csv file
    with open(data_path + '/description.csv', 'a+') as desc_file:
        extra = extrainfo
        desc_file.write(args.config_file + ',' + os.environ["BLINK_SYSTEM"] + ',' + str(num_nodes) + ',' + allocation_mode + ',' +
                        allocation_split + ',' + str(ppn) + ',' + out_format + ',' + extra + ',' + data_directory + '\n')
           
    # prepare runtime feedback output
    ro_file_path = data_directory+'/run_log'
    if ro_mode == 'file' or ro_mode == '+file':
        ro_file = open(ro_file_path, 'w')
    else:
        ro_file = None
    if ro_mode == 'file':
        print('Runtime feedback printed to: '+ro_file_path)

    print_runtime('\nConfig from JSON:', ro_mode, ro_file)
    print_runtime(json.dumps(config, indent=4), ro_mode, ro_file)

    # requiered testing parameters
    num_apps = 0
    apps = []  # list for all apps
    apps_awaited = []  # list of index of apps for which gets waited at end of iteration
    apps_waiting = []  # list of index of apps which wait at end of iteration
    schedule = []  # execution order, holds triples (app_id,action,time_stamp)

    print_runtime('\nApps:', ro_mode, ro_file)

    # --- INIZIO BLOCCO MODIFICATO ---
    # Sostituzione della lettura del file app_mix con l'iterazione sulla configurazione JSON
    # Ordina le applicazioni per chiave ('0', '1', ...) per garantire un ordine di elaborazione prevedibile
    for app_key, app_details in sorted(applications_config.items()):
        import_path_app = app_details.get("path")

        # Se il path e' vuoto, salta questa applicazione, come nell'esempio
        if not import_path_app:
            continue

        app_args = app_details.get("args", "")
        collect_flag = app_details.get("collect", False)
        start_val = app_details.get("start", "")
        end_val = app_details.get("end", "")

        # replace arguments with those specified
        if replace_mix_args and replace_mix_args != "":
            for replacement in replace_mix_args.split(","):
                key, value = replacement.split(":")
                app_args = app_args.replace(key, value)

        # start of app
        if start_val == '':
            start_time = 0
        else:
            start_time = float(start_val)
        schedule += [(num_apps, 's', start_time)]

        # termination of app
        if end_val == '':
            apps_awaited += [num_apps]
            end_cond = 'run until finished'
        elif end_val == 'f':
            apps_waiting += [num_apps]
            end_cond = 'wait until others finished'
        else:
            end_time = float(end_val)
            if end_time < start_time:
                raise Exception('Application '+str(num_apps) +
                                ' must be started before it can be killed.')
            if collect_flag:  # if data should be collected send sigterm
                schedule += [(num_apps, 't', end_time)]
                end_cond = 'terminate at: '+str(end_time)+'s'
            else:  # if no data needs to be collected can just kill
                schedule += [(num_apps, 'k', end_time)]
                end_cond = 'kill at: '+str(end_time)+'s'

        # import app class
        # assumes name is last element of path without .py suffix
        app_class_name = (import_path_app.split(os.sep)[-1])[:-3]
        print(f"The app_class_name is {app_class_name}")
        print(f"The import_path_app is {import_path_app}")
        spec_app = importlib.util.spec_from_file_location(
            app_class_name, import_path_app)
        mod_app = importlib.util.module_from_spec(spec_app)
        spec_app.loader.exec_module(mod_app)
        apps += [mod_app.app(num_apps, collect_flag, app_args)]

        print_runtime((str(num_apps)+': with arguments:"'+app_args+'", collection flag: '+str(collect_flag)
                       + ', ending condition: '+end_cond+'.'), ro_mode, ro_file)

        num_apps += 1
    # --- FINE BLOCCO MODIFICATO ---

    if num_apps == 0:
        raise Exception('Must specify at least one application to run.')

    # import wlm class
    # assumes name is last element of path without .py suffix
    wlm_class_name = (import_path_wlm.split(os.sep)[-1])[:-3]
    spec_wlm = importlib.util.spec_from_file_location(
        wlm_class_name, import_path_wlm)
    mod_wlm = importlib.util.module_from_spec(spec_wlm)
    spec_wlm.loader.exec_module(mod_wlm)
    wlmanager = mod_wlm.wl_manager()

    # assign nodes to apps
    nodes_frame = pandas.read_csv(node_file, header=None, keep_default_na=False, dtype=str)

    # print the node_file content
    with open(node_file, 'r') as f:
        nodes_frame_content = f.read()
        print("[DEBUG] The content of the node file is:\n" + nodes_frame_content)

    #num_elems = nodes_frame.size
    num_cols = nodes_frame.shape[1]
    if num_nodes % num_cols:
        raise Exception('Number of nodes must be a multiple of columns in '+node_file+'.')
    else:
        nodes_frame.head(num_nodes // num_cols)

    if allocation_mode == 'c':  # custom allocation, doesn't need splits
        if num_cols != num_apps:
            raise Exception(
                'Must specify exactly one column of nodes for every application in '+node_file+'.')
        c_allocation(apps, nodes_frame)
    else:
        split_absolute = get_abs_split(allocation_split, num_apps, num_nodes)
        node_list = nodes_frame.iloc[:, 0].tolist()

        print("[DEBUG] The node_list before allocation is: " + str(node_list))
        print("[DEBUG] The apps are: " + str(apps))

        if allocation_mode == 'i':
            i_allocation(apps, node_list, split_absolute)
        else:  # mode 'l' or 'r' or '+r'
            if allocation_mode == 'r' or allocation_mode == '+r':
                random.shuffle(node_list)
            l_allocation(apps, node_list, split_absolute)

    print_runtime('\nNode allocation:', ro_mode, ro_file)
    print_runtime('Processes per node (ppn): '+str(ppn), ro_mode, ro_file)
    for app in apps:
        print_runtime(str(app.id_num)+' on '+str(app.num_nodes) +
                      ' nodes:', ro_mode, ro_file)
        print_runtime(np.array(app.node_list), ro_mode, ro_file)
        f = open(data_directory + os.path.sep + "hostnames_" + str(app.id_num), "w")
        f.write("\n".join(map(str, app.node_list)))
        f.close()

    # On Alps/LUMI, print nodes location
    if os.environ["BLINK_SYSTEM"] == "lumi" or os.environ["BLINK_SYSTEM"] == "alps":
        subprocess.call(["srun", "cat", "/etc/cray/xname"], stdout=open(data_directory + os.path.sep + "xname", "w"))

        
    # sort schedule chronologically
    print_runtime('\nSchedule:', ro_mode, ro_file)
    schedule.sort(key=lambda t: t[2])
    for app_id, action_type, action_time in schedule:
        if action_type == 's':
            print_runtime(str(action_time)+'s: start ' +
                          str(app_id), ro_mode, ro_file)
        elif action_type == 'k':
            print_runtime(str(action_time)+'s: kill ' +
                          str(app_id), ro_mode, ro_file)
        elif action_type == 't':
            print_runtime(str(action_time)+'s: terminate ' +
                          str(app_id), ro_mode, ro_file)

    # prepare data storing
    tot_num_metrics = 0
    data_container_list = []
    for app in apps:
        if app.collect_flag:
            for i in range(len(app.metadata)):
                data_container_list += [data_container(app.id_num, app.metadata[i]["conv"], app.metadata[i]["name"], app.metadata[i]["unit"])]

    # end of preps
    print_runtime('\nPreparing took '+str(round(time.time() -
                  pre_start_time, 5))+'s.', ro_mode, ro_file)
    print_runtime('\nRunning...', ro_mode, ro_file)

    # start running
    runs = 0
    converged = False
    start_time = time.time()
    timeout_occured = False
    while True:
        exec_time = time.time()-start_time
        # termination conditions
        if runs >= max_runs:  # termination condition reached max runs
            print_runtime('Completed maximum number of runs, terminated after '
                          + str(runs)+' runs taking '+str(round(exec_time, 2))+' seconds.', ro_mode, ro_file)
            break
        elif runs >= min_runs and converged:  # termination condition data converged
            print_runtime('Completed after reaching confidence interval, terminated after '
                          + str(runs)+' runs taking '+str(round(exec_time, 2))+' seconds.', ro_mode, ro_file)
            break
        if exec_time >= time_out:  # termination condition timeout inbetween schedules
            print_runtime('Completed after timeout, terminated after '+str(runs)+' runs taking '
                          + str(round(exec_time, 2))+' seconds.', ro_mode, ro_file)
            break
        # not enough time to finish another run of the schedule
        if (time_out-(time.time()-start_time) < (schedule[-1])[2]):
            print_runtime('Completed after timeout (remaining time not enough for schedule), terminated after '
                          + str(runs)+' runs taking '+str(round(exec_time, 2))+' seconds.', ro_mode, ro_file)
            break

        # clean tmp directory
        try:
            for file in os.listdir('./tmp'):
                os.unlink('./tmp/'+file)
        except FileNotFoundError:
            pass

        # execute schedule
        print_runtime(' Run '+str(runs+1)+':', ro_mode, ro_file)
        run_start_time = time.time()

        # reshuffle if needed
        if allocation_mode == '+r':
            random.shuffle(node_list)
            l_allocation(apps, node_list, split_absolute)
            print_runtime('  Reshuffled node allocation.', ro_mode, ro_file)

        current_time = 0
        for app_id, action_type, action_time in schedule:
            time.sleep(action_time-current_time)
            if action_type == 's':
                print_runtime('About to start '+str(app_id)+' at '+str(action_time)+'s.', ro_mode, ro_file)
                run_job(apps[app_id], wlmanager, ppn)
                print_runtime('  '+str(round(time.time()-run_start_time, 5)
                                       )+'s: started '+str(app_id), ro_mode, ro_file)
            elif action_type == 'k':
                end_job(apps[app_id])
                print_runtime('  '+str(round(time.time()-run_start_time, 5)
                                       )+'s: killed '+str(app_id), ro_mode, ro_file)
            elif action_type == 't':
                end_job(apps[app_id])
                print_runtime('  '+str(round(time.time()-run_start_time, 5)) +
                              's: terminated '+str(app_id), ro_mode, ro_file)
            else:
                raise Exception('Unkown schedule action: ' + action_type)
            current_time = action_time

        # wait for all apps that don't get killed
        timeout_occured = False
        for app_ind in apps_awaited:
            remaining_time = time_out-(time.time()-start_time)
            timeout_occured = wait_timed(apps[app_ind], remaining_time)
            if timeout_occured:
                print_runtime('  '+str(round(time.time()-run_start_time, 5)) +
                              's: timed out '+str(app_ind), ro_mode, ro_file)
            else:
                print_runtime('  '+str(round(time.time()-run_start_time, 5)) +
                              's: awaited '+str(app_ind), ro_mode, ro_file)

        # kill all apps that were marked with 'f' and are still waiting
        for app_ind in apps_waiting:
            end_job(apps[app_ind])
            if apps[app_ind].collect_flag:
                print_runtime('  '+str(round(time.time()-run_start_time, 5))+'s: terminated waiting '
                              + str(app_ind), ro_mode, ro_file)
            else:
                print_runtime('  '+str(round(time.time()-run_start_time, 5))+'s: killed waiting '
                              + str(app_ind), ro_mode, ro_file)

        if timeout_occured:
            # timeout occured terminate, don't read data anymore
            print_runtime('  No data collection due to timeout.',
                          ro_mode, ro_file)
            print_runtime('Completed after timeout, terminated after '+str(runs)+' runs taking '
                          + str(round(time.time()-start_time, 2))+' seconds.', ro_mode, ro_file)
            break

        runs += 1

        # Check every app for output
        for app in apps:
            # print_runtime(app.stdout,ro_mode,ro_file)

            # Check if app exited with error
            if hasattr(app, 'process') and app.process.returncode is not None and app.process.returncode > 0:
                print_runtime('  '+str(round(time.time()-run_start_time, 5))+'s: encountered an exception in '
                              + str(app.id_num) + f' (exit code: {app.process.returncode})', ro_mode, ro_file)
                # Stampa stderr solo se c'è stato un errore, è più utile
                if app.stderr != '':
                    print_runtime("--- STDERR ---", ro_mode, ro_file)
                    print_runtime(app.stderr, ro_mode, ro_file)
                    print_runtime("--------------", ro_mode, ro_file)
                raise Exception("Application " + str(app.id_num) + " threw an exception.")


        # get data
        data_collection_start_time = time.time()
        j = 0
        for app in apps:
            if (len(app.metadata) > 0) and app.collect_flag:
                data_list_of_list = app.read_data()
                for data_list in data_list_of_list:
                    data_container_list[j].data += data_list
                    data_container_list[j].num_samples += [len(data_list)]
                    j += 1
        print_runtime('  Data collection took '+str(round(time.time() -
                      data_collection_start_time, 5))+'s.', ro_mode, ro_file)

        # check convergence
        if runs >= min_runs:
            conv_check_start_time = time.time()
            converged = check_CI(data_container_list, alpha,
                                 beta, converge_all, runs)
            print_runtime('  Convergence check took '+str(round(time.time() -
                          conv_check_start_time, 5))+'s.', ro_mode, ro_file)

    # create logs
    log_start_time = time.time()
    #TODO: controllare se data_container_list e' vuoto (Solo header)
    log_data(out_format, data_directory + '/data', data_container_list)
    log_meta_data(out_format, data_directory+'/metadata', data_container_list, runs)
    print_runtime('Writing data & meta-data took ' +
                      str(round(time.time()-log_start_time, 5))+'s.', ro_mode, ro_file)

    print_runtime('Overall took '+str(round(time.time() -
                  pre_start_time, 5))+'s.', ro_mode, ro_file)

    if ro_mode == 'file' or ro_mode == '+file':
        ro_file.close()

    # TODO capire perche' elimina il file dei nodi
    # os.remove(node_file)


if __name__ == '__main__':
    main()
