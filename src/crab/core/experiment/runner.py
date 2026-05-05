import os  
import pathlib  
import importlib.util  
import time  
from typing import List, Dict, Any  
  
from crab.log import CrabLogger  
from crab.core.data.utils import log_data
from ..data import DataContainer, check_CI  
from ..allocation import NodeAllocator  
from ..process import run_job, end_job  
  
class ExperimentRunner:  
    """  
    Manages the lifecycle of a single experiment within the job.  
    Isolates setup, execution, and teardown.  
    """  
    def __init__(self, exp_name: str, config: Dict[str, Any], global_options: Dict[str, Any],   
                 node_list: List[str], output_dir: str, logger: CrabLogger):  
        self.name = exp_name  
        self.config = config  
        self.global_opts = global_options  
        self.node_list = node_list  
        self.log = logger.enter(exp_name)  
          
        # Paths  
        self.exp_dir = os.path.join(output_dir, self.name)  
        os.makedirs(self.exp_dir, exist_ok=True)  

        # Configuration Merge
        local_opts = self.config.get("local_options", {})
        self.exp_opts = {**self.global_opts, **local_opts}

        # State  
        self.apps = []  
        self.wlmanager = None  
        self.data_containers = []  
        # Force PPN to strictly obey the physical global allocation
        self.ppn = int(self.global_opts.get('ppn', 1))
  
    def setup(self):  
        """Loads apps, workload manager, and calculates node layout."""  
        self.log.info("Setting up...")  
          
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
  
            # Controlla la ENV CRAB_PATH_WRAPPERS  
            if not os.path.isabs(path) and "CRAB_PATH_WRAPPERS" in os.environ:  
                path = os.path.join(os.environ["CRAB_PATH_WRAPPERS"], path)  
              
            if not os.path.exists(path):  
                 self.log.error(f"Wrapper not found at: {path}")  
                 raise FileNotFoundError(f"Wrapper not found: {path}")  
  
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
        mode = self.exp_opts.get('allocationmode', 'l')  
          
        # Pass the unified experiment options to the allocator
        alloc_options = self.exp_opts.copy()

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

    def execute(self, data_path):
        """Main execution loop (Setup -> Run -> Wait -> Converge)."""
        self.log.info("Execution started")
        
        # Params
        min_runs = int(self.exp_opts.get('minruns', 10))
        max_runs = int(self.exp_opts.get('maxruns', 20))
        timeout = float(self.exp_opts.get('timeout', 1200.0))
        converge_all = bool(self.exp_opts.get('convergeall', False))
        alpha = float(self.exp_opts.get('alpha', 0.05))
        beta = float(self.exp_opts.get('beta', 0.05))

        # Recupera l'header dalle opzioni globali (dove l'Orchestrator lo ha messo)
        # Default a lista vuota se non esiste. Header is strictly global.
        system_header = self.global_opts.get('system_header', [])

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

                run_log = self.log.enter(f"Run {runs + 1}")
                run_log.info("Started")

                run_start = time.time()
                
                # Reset ephemeral schedule for this run
                curr_schedule = sorted(static_schedule, key=lambda x: x[2])
                curr_deps = dependency_map.copy()
                running = set()
                finished = set()

                f_app_ids = {i for i, app in enumerate(self.apps) if str(app.config_end) == 'f'}

                # Inner Event Loop
                while True:
                    now = time.time() - run_start
                    
                    # 1. Time-based events
                    while curr_schedule and curr_schedule[0][2] <= now:
                        aid, action, _ = curr_schedule.pop(0)
                        if action == 's':
                            if aid not in running:
                                app_log = run_log.enter(f"App {aid}")
                                concurrent = len(static_schedule) > 1 or len(dependency_map) > 0
                                run_job(self.apps[aid], self.wlmanager, self.ppn,
                                        logger=app_log, pre_commands=system_header,
                                        live_stream=concurrent, data_path=data_path)

                                running.add(aid)
                        elif action == 'k':
                            if aid in running:
                                end_job(self.apps[aid], run_log)
                                running.remove(aid)
                                finished.add(aid)
                    # 2. Check process status
                    for aid in list(running):
                        proc = self.apps[aid].process
                        if proc.poll() is not None:
                            app_log = run_log.enter(f"App {aid}")
                            try:
                                # Ensure silent thread is finished reading
                                if hasattr(self.apps[aid], '_stream_thread') and self.apps[aid]._stream_thread:
                                    self.apps[aid]._stream_thread.join(timeout=2.0)

                                # stdout is already consumed by the thread, so communicate() only gets stderr
                                _, err = proc.communicate()
                                
                                # Reconstruct stdout from the buffer
                                out = b"".join(getattr(self.apps[aid], 'raw_stdout_buffer', []))
                                
                                self.apps[aid].set_output(out, err)

                                exit_code = proc.returncode
                                if exit_code != 0:
                                    app_log.error(f"FAILED  exit={exit_code}")

                                    # We STILL forward stderr, because errors should be visible in slurm_output.log
                                    if err:
                                        stderr_text = err.decode('utf-8', errors='replace') if isinstance(err, bytes) else err
                                        app_log.app_output("", stderr_text)

                                    # Write detailed error log to experiment dir
                                    try:
                                        err_path = os.path.join(self.exp_dir, f"error_app_{aid}.log")
                                        with open(err_path, "w") as f:
                                            f.write(f"App {aid} exit={exit_code}\n")
                                            if err:
                                                decoded = err.decode('utf-8', errors='replace') if isinstance(err, bytes) else err
                                                f.write(decoded)
                                    except Exception:
                                        app_log.warning("Could not write error log file")
                                else:
                                    app_log.info(f"FINISHED  exit=0")
                                    # REMOVED: The logic that forwarded 'out' to app_log.app_output
                                    # Data is now silently waiting in self.apps[aid].stdout for the CSV parser.

                            except Exception as e:
                                app_log.error(f"Failed reading output: {e}")

                            running.remove(aid)
                            finished.add(aid)

                    # 3. Check Dependencies
                    started_deps = []
                    for waiter, target in curr_deps.items():
                        if target in finished:
                            dep_log = run_log.enter(f"App {waiter}")
                            run_job(self.apps[waiter], self.wlmanager, self.ppn,
                                    logger=dep_log, pre_commands=system_header,
                                    live_stream=True)
                            running.add(waiter)
                            if waiter in rel_durations:
                                curr_schedule.append((waiter, 'k', now + rel_durations[waiter]))
                                curr_schedule.sort(key=lambda x: x[2])
                            started_deps.append(waiter)
                    for s in started_deps: del curr_deps[s]


                    if not curr_schedule and not curr_deps and not (running - f_app_ids):
                        break
                    
                    # Check if the global elapsed time has exceeded the timeout
                    if (time.time() - global_start) >= timeout:
                        run_log.error(f"HARD TIMEOUT: Experiment exceeded {timeout}s mid-run.")
                        for active_aid in list(running):
                            try:
                                self.apps[active_aid].process.kill()
                            except:
                                pass
                        break # Break the inner loop, forcing a teardown
                    
                    time.sleep(0.05)

                # ── Lorenzo's modifications ──────────────────────────────
                # Kill "f" apps now that all other work is done
                for i, app in enumerate(self.apps):
                    if str(app.config_end) == 'f':
                        if hasattr(app, 'process') and app.process.poll() is None:
                            end_job(app, run_log)
                # ─────────────────────────────────────────────────────────

                #! Lorenzo's ping: it is better to collect the data while we are polling, or we need to print some [INFO] logs to understand it is running or not
                #! read_data is defined from the wrapper, we need to make it clear
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
                    if converged:
                        self.log.info(f"Converged at run {runs}")

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
            out_fmt = self.exp_opts.get('outformat', 'csv')
            prefix = os.path.join(self.exp_dir, 'data')
            log_data(out_fmt, prefix, self.data_containers)
            self.log.info(f"Data saved to {self.exp_dir}")
