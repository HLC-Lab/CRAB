import subprocess
import numpy as np
import scipy.stats as st
import math
import time
import importlib.util
import pathlib
import sys
import os
import datetime
import pandas
import shlex
import json
import shutil
from typing import List, Dict, Any, Callable, Optional, Union

# =============================================================================
# 1. DATA CONTAINERS & UTILITIES
# =============================================================================

class DataContainer:
    """Holds runtime metrics for a specific application."""
    def __init__(self, app_id: int, conv_goal: bool, label: str, unit: str, msg_size: int = 0):
        self.app_id = app_id
        self.conv_run = 0
        self.label = label
        self.unit = unit
        self.conv_goal = conv_goal
        self.converged = False
        self.num_samples = []
        self.data = []
        self.msg_size = msg_size

    def get_title(self) -> str:
        return f"{self.app_id}_{self.label}_{self.unit}"

    def md_to_list(self) -> List[Any]:
        return [self.app_id, self.label, self.unit, self.conv_goal, self.converged, self.conv_run, self.msg_size] + self.num_samples

def check_CI(container_list: List[DataContainer], alpha: float, beta: float, converge_all: bool, run: int) -> bool:
    """Checks statistical convergence based on Confidence Intervals (CI)."""
    for container in container_list:
        if (not container.converged) and (converge_all or container.conv_goal):
            n = len(container.data)
            if n <= 1: continue 
            
            mean = np.mean(container.data)
            sem = st.sem(container.data)
            
            if sem == 0:
                container.converged = True
                container.conv_run = run
                continue
            
            CI_lb, CI_ub = st.t.interval(1 - alpha, n - 1, loc=mean, scale=sem)
            if (CI_ub - CI_lb) < beta * mean:
                container.converged = True
                container.conv_run = run

    check = True
    for container in container_list:
        if (converge_all or container.conv_goal):
            check = check and container.converged
    return check

def run_job(job, wlmanager, ppn: int):
    """launches an application process via the workload manager."""
    if not job.node_list:
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")
    
    cmd_string = wlmanager.run_job(job.node_list, ppn, job.run_app())
    if not cmd_string:
        cmd_string = "echo a > /dev/null"
    
    cmd = shlex.split(cmd_string)
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    job.set_process(process)

def end_job(job):
    """Forcefully terminates a job and retrieves output."""
    if hasattr(job, 'process') and job.process:
        job.process.kill()
        out, err = job.process.communicate()
        job.set_output(out, err)

def wait_timed(job, timeout_sec: float) -> bool:
    """Waits for a job with a timeout. Returns True if timed out."""
    try:
        out, err = job.process.communicate(timeout=timeout_sec)
        job.set_output(out, err)
        return False
    except subprocess.TimeoutExpired:
        end_job(job)
        return True

def log_data(out_format: str, path_prefix: str, data_containers: List[DataContainer]):
    """Aggregates and saves data to CSV or HDF."""
    apps_data = {}
    for container in data_containers:
        apps_data.setdefault(container.app_id, []).append(container)

    for app_id, containers in apps_data.items():
        all_metrics = []
        app_msg_size = containers[0].msg_size if containers else 0

        for container in containers:
            if not container.data or not container.num_samples: continue

            # Reconstruct run_id column
            run_ids = []
            for i, num in enumerate(container.num_samples):
                run_ids.extend([i + 1] * num)

            # Truncate mismatch
            min_len = min(len(run_ids), len(container.data))
            run_ids = run_ids[:min_len]
            container.data = container.data[:min_len]

            df = pandas.DataFrame({'run_id': run_ids, container.get_title(): container.data})
            df = df.set_index(['run_id', df.groupby('run_id').cumcount()])
            all_metrics.append(df)

        if not all_metrics: continue

        dataframe = pandas.concat(all_metrics, axis=1).reset_index()
        if 'level_1' in dataframe.columns: dataframe = dataframe.drop(columns=['level_1'])
        
        dataframe.insert(1, "msg_size", app_msg_size)
        
        file_name = f"{path_prefix}_app_{app_id}"
        if out_format == 'csv':
            dataframe.to_csv(f"{file_name}.csv", index=False)
        elif out_format == 'hdf':
            dataframe.to_hdf(f"{file_name}.h5", key='df', index=False)

# =============================================================================
# 2. NODE ALLOCATOR LOGIC
# =============================================================================

class NodeAllocator:
    """Encapsulates all strategies for mapping nodes to applications."""

    @staticmethod
    def get_abs_split(split_str: str, num_apps: int, num_nodes: int) -> List[int]:
        """Calculates absolute node counts based on percentage or equal split."""
        if split_str == 'e':
            split_list = [100.0 / num_apps] * num_apps
        else:
            split_list = [float(x) for x in split_str.split(':')]

        if sum(split_list) > 100.1: # float tolerance
            raise Exception("Splits percentages exceed 100.")
        
        split_list = split_list[:num_apps]
        split_absolute = []
        for split in split_list[:-1]:
            split_absolute.append(int(math.ceil(num_nodes * split / 100)))
        
        if num_apps == 1:
            split_absolute = [int(math.ceil(num_nodes * split_list[0] / 100))]
        else:
            split_absolute.append(num_nodes - sum(split_absolute))
            
        return split_absolute

    @staticmethod
    def allocate_linear(apps: List[Any], node_list: List[str], split_counts: List[int]):
        """Allocates contiguous blocks of nodes to applications."""
        idx = 0
        for app, count in zip(apps, split_counts):
            app.set_nodes(node_list[idx : idx + count])
            idx += count

    @staticmethod
    def allocate_interleaved(apps: List[Any], node_list: List[str], split_counts: List[int]):
        """Allocates nodes in a round-robin fashion."""
        num_apps = len(apps)
        alloc_lists = [[] for _ in range(num_apps)]
        counts_copy = list(split_counts)
        
        app_idx = 0
        node_idx = 0
        
        # While there are nodes to assign and demand exists
        while any(counts_copy) and node_idx < len(node_list):
            if counts_copy[app_idx] > 0:
                alloc_lists[app_idx].append(node_list[node_idx])
                counts_copy[app_idx] -= 1
                node_idx += 1
            app_idx = (app_idx + 1) % num_apps

        for app, a_list in zip(apps, alloc_lists):
            app.set_nodes(a_list)

    @staticmethod
    def allocate_partitioned(apps: List[Any], node_list: List[str], options: Dict[str, Any]):
        """
        Advanced allocation: divides nodes into partitions (Victim/Aggressor) 
        and applies sub-rules (Shared vs Dedicated) within partitions.
        """
        num_nodes = len(node_list)
        partition_split = options.get('partitionsplit', '100')
        layout = options.get('partitionlayout', 'l')
        local_rules = [x.strip() for x in options.get('allocationsplit', 'e').split('-')]

        # 1. Determine Partition Sizes
        if partition_split == 'e':
            # Auto-detect based on app partition_ids
            used_ids = set(getattr(a, 'partition_id', 0) for a in apps)
            max_p = max(used_ids) + 1 if used_ids else 1
            pt_counts = [int(math.ceil(num_nodes / max_p)) for _ in range(max_p)]
            # Adjust remainder
            diff = sum(pt_counts) - num_nodes
            if diff != 0: pt_counts[-1] -= diff
        else:
            percs = [float(x) for x in partition_split.split(':')]
            pt_counts = [int(math.ceil(num_nodes * p / 100)) for p in percs[:-1]]
            pt_counts.append(num_nodes - sum(pt_counts))

        # 2. Assign nodes to Partitions (Linear vs Interleaved)
        partitions_nodes = [[] for _ in range(len(pt_counts))]
        
        if layout == 'i':
            node_idx = 0
            while node_idx < num_nodes:
                for p_idx in range(len(pt_counts)):
                    if len(partitions_nodes[p_idx]) < pt_counts[p_idx]:
                        partitions_nodes[p_idx].append(node_list[node_idx])
                        node_idx += 1
                        if node_idx >= num_nodes: break
        else:
            idx = 0
            for p_idx, count in enumerate(pt_counts):
                partitions_nodes[p_idx] = node_list[idx : idx + count]
                idx += count

        # 3. Apply Local Rules to Apps in each Partition
        if len(local_rules) == 1 and len(partitions_nodes) > 1:
            local_rules = local_rules * len(partitions_nodes)

        for p_id, (p_nodes, p_rule) in enumerate(zip(partitions_nodes, local_rules)):
            p_apps = [a for a in apps if getattr(a, 'partition_id', 0) == p_id]
            if not p_apps: continue

            # Shared Mode ('100' or single app 'e')
            if p_rule == '100' or (p_rule == 'e' and len(p_apps) <= 1):
                for app in p_apps:
                    app.set_nodes(p_nodes)
            else:
                # Space Sharing within partition
                sub_split = NodeAllocator.get_abs_split(p_rule, len(p_apps), len(p_nodes))
                NodeAllocator.allocate_linear(p_apps, p_nodes, sub_split)

# =============================================================================
# 3. EXPERIMENT RUNNER (Context for a single experiment)
# =============================================================================

class ExperimentRunner:
    """
    Manages the lifecycle of a single experiment within the job.
    Isolates setup, execution, and teardown.
    """
    def __init__(self, exp_name: str, config: Dict[str, Any], global_options: Dict[str, Any], 
                 node_list: List[str], output_dir: str, log_fn: Callable):
        self.name = exp_name
        self.config = config
        self.global_opts = global_options
        self.node_list = node_list
        self.log = log_fn
        
        # Paths
        self.exp_dir = os.path.join(output_dir, self.name)
        os.makedirs(self.exp_dir, exist_ok=True)
        
        # State
        self.apps = []
        self.wlmanager = None
        self.data_containers = []
        self.ppn = int(global_options.get('ppn', 1))

    def setup(self):
        """Loads apps, workload manager, and calculates node layout."""
        self.log(f"[{self.name}] Setting up...")
        
        # 1. Load Applications
        self.apps = []
        app_configs = self.config.get("apps", {})
        sorted_keys = sorted(app_configs.keys(), key=lambda x: int(x) if x.isdigit() else x)
        
        # Dependency tracking
        dependency_map = {}
        static_schedule = []
        
        # Helper to load modules
        def load_module(path):
            name = pathlib.Path(path).stem
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        # WLM Loading
        wlm_name = os.environ.get("CRAB_WL_MANAGER", "slurm") # default
        wlm_path = f"./src/crab/core/wl_manager/{wlm_name}.py"
        self.wlmanager = load_module(wlm_path).wl_manager()

        # App Instantiation
        idx_counter = 0
        for key in sorted_keys:
            details = app_configs[key]
            path = details.get("path")
            if not path: continue

            # Load App Class
            mod_app = load_module(path)
            args = details.get("args", "")
            collect = details.get("collect", False)
            
            app_instance = mod_app.app(idx_counter, collect, args)
            
            # Timing & Partition Metadata
            start_val = str(details.get("start", "0"))
            manual_partition = details.get("partition")
            app_instance.partition_id = int(manual_partition) if manual_partition is not None else (0 if collect else 1)
            app_instance.start_string = start_val
            app_instance.config_end = details.get("end", "")
            
            self.apps.append(app_instance)
            idx_counter += 1

        # 2. Allocate Nodes
        mode = self.global_opts.get('allocationmode', 'l')
        
        # Merge experiment specific overrides into options for allocator
        alloc_options = self.global_opts.copy()
        # (Future: allow experiment config to override global_opts for splitting)
        
        if mode == 'p':
            NodeAllocator.allocate_partitioned(self.apps, self.node_list, alloc_options)
        elif mode == 'i':
            split = NodeAllocator.get_abs_split(alloc_options.get('allocationsplit', 'e'), len(self.apps), len(self.node_list))
            NodeAllocator.allocate_interleaved(self.apps, self.node_list, split)
        else: # linear
            split = NodeAllocator.get_abs_split(alloc_options.get('allocationsplit', 'e'), len(self.apps), len(self.node_list))
            NodeAllocator.allocate_linear(self.apps, self.node_list, split)

        # 3. Initialize Data Containers
        for app in self.apps:
            if app.collect_flag:
                # Parse msg_size if present in args (for logging)
                msg_size = 0
                tokens = str(app.args).split()
                if "-msgsize" in tokens:
                    try: 
                        msg_size = int(tokens[tokens.index("-msgsize")+1])
                    except: pass
                
                for meta in app.metadata:
                    self.data_containers.append(
                        DataContainer(app.id_num, meta["conv"], meta["name"], meta["unit"], msg_size)
                    )

    def execute(self):
        """Main execution loop (Setup -> Run -> Wait -> Converge)."""
        self.log(f"[{self.name}] Execution started.")
        
        # Params
        min_runs = int(self.global_opts.get('minruns', 10))
        max_runs = int(self.global_opts.get('maxruns', 20))
        timeout = float(self.global_opts.get('timeout', 1200.0))
        converge_all = bool(self.global_opts.get('convergeall', False))
        alpha = float(self.global_opts.get('alpha', 0.05))
        beta = float(self.global_opts.get('beta', 0.05))

        # Schedule Logic Preparation
        dependency_map = {}
        static_schedule = []
        rel_durations = {}
        
        # Build Schedule
        for i, app in enumerate(self.apps):
            # Start
            if app.start_string.startswith('s'):
                dependency_map[i] = int(app.start_string[1:])
            else:
                static_schedule.append((i, 's', float(app.start_string)))
            
            # End
            if app.config_end and app.config_end != 'f':
                val = float(app.config_end)
                if app.start_string.startswith('s'):
                     rel_durations[i] = val
                else:
                    static_schedule.append((i, 'k', val))

        runs = 0
        global_start = time.time()
        converged = False

        try:
            while True:
                # Exit conditions
                elapsed = time.time() - global_start
                if runs >= max_runs or (runs >= min_runs and converged) or elapsed >= timeout:
                    break

                self.log(f"[{self.name}] Run {runs+1}...")
                run_start = time.time()
                
                # Reset ephemeral schedule for this run
                curr_schedule = sorted(static_schedule, key=lambda x: x[2])
                curr_deps = dependency_map.copy()
                running = set()
                finished = set()

                # Inner Event Loop
                while True:
                    now = time.time() - run_start
                    
                    # 1. Time-based events
                    while curr_schedule and curr_schedule[0][2] <= now:
                        aid, action, _ = curr_schedule.pop(0)
                        if action == 's':
                            if aid not in running:
                                run_job(self.apps[aid], self.wlmanager, self.ppn)
                                running.add(aid)
                        elif action == 'k':
                            if aid in running:
                                end_job(self.apps[aid])
                                running.remove(aid)
                                finished.add(aid)

                    # 2. Check process status
                    for aid in list(running):
                        if self.apps[aid].process.poll() is not None:
                            # Ended naturally
                            try:
                                out, err = self.apps[aid].process.communicate()
                                self.apps[aid].set_output(out, err)
                            except: pass
                            running.remove(aid)
                            finished.add(aid)

                    # 3. Check Dependencies
                    started_deps = []
                    for waiter, target in curr_deps.items():
                        if target in finished:
                            run_job(self.apps[waiter], self.wlmanager, self.ppn)
                            running.add(waiter)
                            if waiter in rel_durations:
                                curr_schedule.append((waiter, 'k', now + rel_durations[waiter]))
                                curr_schedule.sort(key=lambda x: x[2])
                            started_deps.append(waiter)
                    for s in started_deps: del curr_deps[s]

                    if not curr_schedule and not curr_deps and not running:
                        break # Run finished
                    
                    time.sleep(0.05)

                # Collect Data
                c_idx = 0
                for app in self.apps:
                    if app.collect_flag and hasattr(app, 'process') and app.process.returncode == 0:
                        raw_data = app.read_data()
                        for series in raw_data:
                            self.data_containers[c_idx].data.extend(series)
                            self.data_containers[c_idx].num_samples.append(len(series))
                            c_idx += 1

                runs += 1
                if runs >= min_runs:
                    converged = check_CI(self.data_containers, alpha, beta, converge_all, runs)

        finally:
            self.teardown()

    def teardown(self):
        """Ensures all processes are killed before next experiment."""
        for app in self.apps:
            if hasattr(app, 'process') and app.process:
                if app.process.poll() is None:
                    try: app.process.kill() 
                    except: pass

    def save_results(self):
        """Persists data to disk."""
        if self.data_containers:
            out_fmt = self.global_opts.get('outformat', 'csv')
            prefix = os.path.join(self.exp_dir, 'data')
            log_data(out_fmt, prefix, self.data_containers)
            self.log(f"[{self.name}] Data saved to {self.exp_dir}")

# =============================================================================
# 4. ENGINE (Orchestrator & Worker Entry Point)
# =============================================================================

class Engine:
    def __init__(self, log_callback: Callable[[str], None] = print):
        self.log = log_callback

    def run(self, config: Dict[str, Any], environment: Dict[str, Any], is_worker: bool = False, output_dir: str = None):
        if is_worker:
            self._run_worker(config, environment, output_dir)
        else:
            self._run_orchestrator(config, environment)

    def _run_orchestrator(self, config: Dict[str, Any], environment: Dict[str, Any]):
        """Parses config, creates directories, and submits the SLURM job."""
        self.log("Engine running in ORCHESTRATOR mode.")
        
        # Legacy support: if 'experiments' missing, wrap 'applications' into a default experiment
        if "experiments" not in config:
            if "applications" in config:
                config["experiments"] = {"default_ex": {"apps": config.pop("applications")}}
            else:
                raise ValueError("Config must contain 'experiments' or 'applications'.")

        g_opts = config.get('global_options', {})
        data_path = g_opts.get('datapath', './data')
        num_nodes = int(g_opts.get('numnodes'))
        ppn = int(g_opts.get('ppn', 1))
        
        # Setup Directory
        desc_file = os.path.join(data_path, "description.csv")
        os.makedirs(data_path, exist_ok=True)
        if not os.path.isfile(desc_file):
            with open(desc_file, 'w') as f:
                f.write('system,numnodes,extra,path\n')

        # Unique ID
        runner_id = (environment.get("CRAB_SYSTEM", "unknown") + "/" + 
                     datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f'))
        data_directory = os.path.join(data_path, runner_id)
        os.makedirs(data_directory, exist_ok=True)

        with open(desc_file, 'a+') as f:
            f.write(f"{environment.get('CRAB_SYSTEM')},{num_nodes},{g_opts.get('extrainfo')},{data_directory}\n")

        # Save context for worker
        with open(os.path.join(data_directory, 'config.json'), 'w') as f:
            json.dump(config, f, indent=4)
        with open(os.path.join(data_directory, 'environment.json'), 'w') as f:
            json.dump(environment, f, indent=4)

        # Generate SBATCH
        script_path = os.path.join(data_directory, 'crab_job.sh')
        cmd = f"{sys.executable} {os.path.abspath(sys.argv[0])} --worker --workdir {data_directory}"
        
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\n\n")
            f.write(f"#SBATCH --job-name=crab_{g_opts.get('extrainfo', '')[:10]}\n")
            f.write(f"#SBATCH --output={os.path.join(data_directory, 'slurm_output.log')}\n")
            f.write(f"#SBATCH --error={os.path.join(data_directory, 'slurm_error.log')}\n")
            f.write(f"#SBATCH --nodes={num_nodes}\n")
            f.write(f"#SBATCH --ntasks-per-node={ppn}\n")
            f.write(f"#SBATCH --time={g_opts.get('walltime', '01:00:00')}\n")
            
            # Platform specific (Example: Leonardo)
            if environment.get("CRAB_SYSTEM") == "leonardo":
                f.write("#SBATCH --partition=boost_usr_prod\n")
                f.write("#SBATCH --account=IscrB_SWING\n")
                f.write("#SBATCH --gres=tmpfs:0\n")

            venv = os.path.join(os.getcwd(), '.venv/bin/activate')
            if os.path.exists(venv):
                f.write(f"\nsource {venv}\n")
            
            f.write(f"\n{cmd}\n")

        # Submit
        self.log(f"Submitting: sbatch {script_path}")
        out = subprocess.check_output(['sbatch', script_path], text=True)
        self.log(out.strip())

    def _run_worker(self, config: Dict[str, Any], environment: Dict[str, Any], output_dir: str):
        """Worker mode: Sequentially runs experiments in the allocated job."""
        self.log("--- [WORKER] Started ---")
        
        # Restore Environment
        orig_env = os.environ.copy()
        os.environ.update(environment)
        
        try:
            # 1. Acquire Resources (Nodes)
            # We parse the SLURM node list once for the whole job.
            node_file = "worker_nodelist.txt"
            with open(node_file, "w") as f:
                subprocess.call(["scontrol", "show", "hostnames", os.environ.get('SLURM_NODELIST')], stdout=f)
            nodes_df = pandas.read_csv(node_file, header=None)
            full_node_list = nodes_df.iloc[:, 0].tolist()
            
            global_opts = config.get('global_options', {})
            experiments = config.get('experiments', {})
            
            # Sort experiments to ensure deterministic order (ex0, ex1, ex2...)
            sorted_exp_ids = sorted(experiments.keys())

            # 2. Sequential Execution Loop
            for exp_id in sorted_exp_ids:
                exp_config = experiments[exp_id]
                self.log(f"\n=== Starting Experiment: {exp_id} ===")
                
                # Instantiate Runner
                runner = ExperimentRunner(
                    exp_name=exp_id,
                    config=exp_config,
                    global_options=global_opts,
                    node_list=full_node_list,
                    output_dir=output_dir,
                    log_fn=self.log
                )
                
                try:
                    runner.setup()
                    runner.execute()
                    runner.save_results()
                except Exception as e:
                    self.log(f"[ERROR] Experiment {exp_id} failed: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    # Defensive Cleanup between experiments
                    runner.teardown()
                    time.sleep(2) # Allow OS to reclaim sockets/handles
            
            self.log("--- [WORKER] All experiments finished ---")

        finally:
            os.environ.clear()
            os.environ.update(orig_env)
            if os.path.exists("worker_nodelist.txt"):
                os.remove("worker_nodelist.txt")
