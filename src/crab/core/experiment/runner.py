import os
import pathlib
import csv
import re
import fcntl
import importlib.util
import shutil
import signal
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

        # Configuration Merge — shallow: a local 'allocation' key replaces the global one entirely.
        # A partial local override (e.g. just {mode: "random"}) will drop global partitions.
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
        wlm_name = os.environ.get("CRAB_WL_MANAGER", "slurm")
        _ALLOWED_WLM = {'slurm', 'mpi', 'workerpool'}
        if wlm_name not in _ALLOWED_WLM:
            raise ValueError(
                f"Unknown CRAB_WL_MANAGER value: {wlm_name!r}. "
                f"Allowed: {sorted(_ALLOWED_WLM)}"
            )

        # Anchor the path dynamically to this script's location
        # __file__ is .../src/crab/core/experiment/runner.py
        # Walking up one level takes us to .../src/crab/core/
        core_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        wlm_path = os.path.join(core_dir, "wl_manager", f"{wlm_name}.py")

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
            
            # Instantiate the app
            app_instance = mod_app.app(idx_counter, collect, args) 

            # --- DYNAMIC CONFIG INJECTION ---
            # Any key in the JSON 'apps' config that isn't a reserved CRAB keyword
            # gets injected as an attribute into the app instance.
            reserved_keys = ["path", "args", "collect", "start", "end", "partition"]
            for key, value in details.items():
                if key not in reserved_keys:
                    setattr(app_instance, key, value)
            # --------------------------------

            
            # --- ARCHITECTURE GUARDRAIL ---
            receipt = app_instance.get_receipt()
            target_arch = receipt.get("target_arch") if receipt else None
            
            if target_arch == "gpu":
                # Check for GPU-specific SLURM flags or environment variables
                sd = self.global_opts.get("sbatch_directives", {})
                if isinstance(sd, dict):
                    partition = sd.get("partition", "")
                elif isinstance(sd, list):
                    partition = ""
                    for _d in sd:
                        _m = re.match(r'--partition[= ](\S+)', str(_d))
                        if _m:
                            partition = _m.group(1)
                            break
                else:
                    partition = ""
                if "cpu" in partition:
                     raise RuntimeError(f"Architecture Mismatch: {receipt.get('name', 'app')} is built for GPU but partition is {partition}")
            # ------------------------------
              
            # Timing & Partition Metadata  
            start_val = str(details.get("start", "0"))  
            manual_partition = details.get("partition")
            app_instance.partition_id = manual_partition  # string name or None
            app_instance.start_string = start_val  
            app_instance.config_end = details.get("end", "")  
              
            self.apps.append(app_instance)  
            idx_counter += 1  
  
        # 2. Allocate Nodes
        allocation = self.exp_opts.get('allocation', {})

        if 'partitions' in allocation:
            NodeAllocator.allocate_partitioned(self.apps, self.node_list, allocation)
        else:
            mode = allocation.get('mode', 'linear')
            split_val = allocation.get('split', 'even')
            split = NodeAllocator.get_abs_split(split_val, len(self.apps), len(self.node_list))
            if mode == 'interleaved':
                NodeAllocator.allocate_interleaved(
                    self.apps, self.node_list, split, stride=allocation.get('stride', 1)
                )
            elif mode == 'random':
                NodeAllocator.allocate_random(
                    self.apps, self.node_list, split, seed=allocation.get('seed')
                )
            else:  # linear (default)
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

        # Tracking states initialized before the loop iterations begin
        experiment_status = "COMPLETED"
        
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

                run_successful = True

                for app in self.apps:
                    app.run_dir = os.path.join(self.exp_dir, f"run_{runs + 1}")
                    os.makedirs(app.run_dir, exist_ok=True)
                
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
                                
                                # --- Merge Hooks and Override Launcher ---
                                merged_pre_commands = self.apps[aid].get_pre_commands()
                                launcher_override = self.apps[aid].get_launcher_override()

                                run_job(self.apps[aid], self.wlmanager, self.ppn,
                                        logger=app_log, pre_commands=merged_pre_commands,
                                        live_stream=concurrent, data_path=data_path,
                                        launcher=launcher_override)

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
                                    run_successful = False

                                    if experiment_status != "TIMEOUT":
                                        experiment_status = "FAILED"

                                    # Extract both streams
                                    stdout_text = out.decode('utf-8', errors='replace') if isinstance(out, bytes) else out
                                    stderr_text = err.decode('utf-8', errors='replace') if isinstance(err, bytes) else err

                                    # Forward BOTH streams to the console if they exist
                                    if stdout_text.strip():
                                        app_log.app_output("STDOUT Dump:", stdout_text)
                                    if stderr_text and stderr_text.strip():
                                        app_log.app_output("STDERR Dump:", stderr_text)

                                    # Write detailed error log to experiment dir, including both streams
                                    try:
                                        err_path = os.path.join(self.exp_dir, f"error_app_{aid}.log")
                                        with open(err_path, "w") as f:
                                            f.write(f"App {aid} exit={exit_code}\n")
                                            if stdout_text.strip():
                                                f.write(f"\n--- STDOUT ---\n{stdout_text}\n")
                                            if stderr_text and stderr_text.strip():
                                                f.write(f"\n--- STDERR ---\n{stderr_text}\n")
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
                            
                            # --- Merge Hooks and Override Launcher ---
                            merged_pre_commands = self.apps[waiter].get_pre_commands()
                            launcher_override = self.apps[waiter].get_launcher_override()

                            run_job(self.apps[waiter], self.wlmanager, self.ppn,
                                    logger=dep_log, pre_commands=merged_pre_commands,
                                    live_stream=True, data_path=data_path,
                                    launcher=launcher_override)
                                    
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
                        experiment_status = "TIMEOUT"
                        for active_aid in list(running):
                            try:
                                os.killpg(os.getpgid(self.apps[active_aid].process.pid), signal.SIGKILL)
                            except OSError:
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

                # Remove run directories whose only visible content is the hidden
                # .wrappers/ build artefact — they appear empty in directory listings.
                if self.apps:
                    _run_dir = getattr(self.apps[0], 'run_dir', None)
                    if _run_dir and os.path.exists(_run_dir):
                        if not any(f for f in os.listdir(_run_dir) if not f.startswith('.')):
                            shutil.rmtree(_run_dir, ignore_errors=True)

                #! Lorenzo's ping: it is better to collect the data while we are polling, or we need to print some [INFO] logs to understand it is running or not
                #! read_data is defined from the wrapper, we need to make it clear
                # Collect Data
                c_idx = 0
                for app in self.apps:
                    if app.collect_flag:
                        num_meta = len(app.metadata)
                        if hasattr(app, 'process') and app.process.returncode == 0:
                            raw_data = app.read_data()
                            for i, series in enumerate(raw_data):
                                if c_idx + i < len(self.data_containers):
                                    self.data_containers[c_idx + i].data.extend(series)
                                    self.data_containers[c_idx + i].num_samples.append(len(series))
                        c_idx += num_meta

                # Clean Dirs Policy
                # Default to True for maximum data safety if the flag is missing
                retain_files = bool(self.exp_opts.get("retain_files", True))

                if not retain_files and run_successful:
                    # Target the shared run directory container
                    # Using the directory path from the first app in the schedule
                    if self.apps:
                        target_run_dir = getattr(self.apps[0], "run_dir", None)
                        if target_run_dir and os.path.exists(target_run_dir):
                            # ignore_errors=True prevents transient parallel filesystem locks 
                            # from crashing the orchestrator loop
                            shutil.rmtree(target_run_dir, ignore_errors=True)

                runs += 1
                if runs >= min_runs:
                    converged = check_CI(self.data_containers, alpha, beta, converge_all, runs)
                    if converged:
                        self.log.info(f"Converged at run {runs}")

        finally:
            self.teardown()

        self._write_to_registry(status=experiment_status)

    def teardown(self):
        """Ensures all processes are killed before next experiment."""
        for app in self.apps:
            if hasattr(app, 'process') and app.process:
                if app.process.poll() is None:
                    try:
                        os.killpg(os.getpgid(app.process.pid), signal.SIGKILL)
                    except OSError:
                        pass

    def save_results(self):
        """Persists data to disk."""
        if self.data_containers:
            out_fmt = self.exp_opts.get('outformat', 'csv')
            prefix = os.path.join(self.exp_dir, 'data')
            log_data(out_fmt, prefix, self.data_containers)
            self.log.info(f"Data saved to {self.exp_dir}")


    def _write_to_registry(self, status):
        """
        Appends a data row for this experiment to the system-level metadata.csv.
        Uses exclusive POSIX file locking to guarantee process safety on shared HPC filesystems.
        """
        try:
            # Traversal: self.exp_dir is system/job_name_timestamp/experiment_name
            job_dir = os.path.dirname(self.exp_dir)
            system_dir = os.path.dirname(job_dir)
            registry_path = os.path.join(system_dir, "metadata.csv")

            job_basename = os.path.basename(job_dir)
            exp_basename = os.path.basename(self.exp_dir)

            # Extract standard ISO-like timestamp from the job folder suffix
            timestamp = "unknown"
            ts_match = re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", job_basename)
            if ts_match:
                timestamp = ts_match.group(0)

            # Safely gather metadata parameters
            job_name = self.global_opts.get("name", "unknown")
            numnodes = self.global_opts.get("numnodes", 1)
            
            # Target ppn across common layout dictionary keys
            ppn = getattr(self, "ppn", "unknown")

            # Space-separated, alphabetically sorted unique application identifiers
            unique_apps = sorted(list(set([
                str(getattr(app, "benchmark_id", getattr(app, "name", "unknown")))
                for app in self.apps
            ])))
            apps_list = " ".join(unique_apps)

            tags = self.global_opts.get("tags", "none")
            relative_path = f"./{job_basename}/{exp_basename}"

            headers = [
                "job_name", "experiment_name", "timestamp", "numnodes", 
                "ppn", "apps_list", "status", "tags", "relative_path"
            ]
            row = [
                job_name, exp_basename, timestamp, numnodes, 
                ppn, apps_list, status, tags, relative_path
            ]

            # Atomic append routine using advisory locking
            with open(registry_path, "a+", newline="") as f:
                # Acquire exclusive lock. Blocks execution until other CRAB instances release it.
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)

                # Move pointer to check if file is completely new/empty
                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    writer = csv.writer(f)
                    writer.writerow(headers)

                writer = csv.writer(f)
                writer.writerow(row)

                # Force filesystem sync before clearing the block lock
                f.flush()
                os.fsync(f.fileno())

                # Release lock explicitly
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        except Exception as e:
            # Fallback guardrail to prevent a registry I/O bottleneck from crashing a study
            self.log.error(f"CRAB Registry execution hook failed: {e}")
