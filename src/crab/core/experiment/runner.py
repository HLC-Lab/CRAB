import csv
import fcntl
import os
import re
import shutil
import signal
import time
from typing import Any

from crab.core.config_checks import parse_bool
from crab.core.data.parse import collect_run, setup_containers
from crab.core.data.utils import log_data
from crab.core.wrapper_paths import load_module, resolve_wrapper_path
from crab.log import CrabLogger
from crab.wrappers.base import base

from ..allocation import NodeAllocator
from ..data import check_CI
from ..process import end_job, run_job
from .artifacts import copy_artifacts


class ExperimentRunner:
    """
    Manages the lifecycle of a single experiment within the job.
    Isolates setup, execution, and teardown.
    """

    def __init__(
        self,
        exp_name: str,
        config: dict[str, Any],
        global_options: dict[str, Any],
        node_list: list[str],
        output_dir: str,
        logger: CrabLogger,
    ):
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
        self.ppn = int(self.global_opts.get("ppn", 1))

    def setup(self):
        """Loads apps, workload manager, and calculates node layout."""
        self.log.info("Setting up...")

        # 1. Load Applications
        self.apps = []
        app_configs = self.config.get("apps", {})
        sorted_keys = sorted(app_configs.keys(), key=lambda x: int(x) if x.isdigit() else x)

        # Helper to load modules
        # WLM Loading
        wlm_name = os.environ.get("CRAB_WL_MANAGER", "slurm")
        _ALLOWED_WLM = {"slurm", "mpi", "workerpool", "local"}
        if wlm_name not in _ALLOWED_WLM:
            raise ValueError(
                f"Unknown CRAB_WL_MANAGER value: {wlm_name!r}. Allowed: {sorted(_ALLOWED_WLM)}"
            )

        # Anchor the path dynamically to this script's location
        # __file__ is .../src/crab/core/experiment/runner.py
        # Walking up one level takes us to .../src/crab/core/
        core_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        wlm_path = os.path.join(core_dir, "wl_manager", f"{wlm_name}.py")

        self.wlmanager = load_module(wlm_path).wl_manager()

        # App Instantiation
        idx_counter = 0
        for key in sorted_keys:
            details = app_configs[key]
            path = details.get("path")
            if not path:
                continue

            path = resolve_wrapper_path(path)

            if not os.path.exists(path):
                self.log.error(f"Wrapper not found at: {path}")
                raise FileNotFoundError(f"Wrapper not found: {path}")

            # Load App Class
            mod_app = load_module(path)
            args = details.get("args", "")
            collect = parse_bool(details.get("collect", False), "collect")

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

            # Wrappers that launch through base.run_app need a binary: find it now so a
            # missing one fails the experiment before any run starts.
            if type(app_instance).run_app is base.run_app:
                app_instance.resolve_binary()

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
                        _m = re.match(r"--partition[= ](\S+)", str(_d))
                        if _m:
                            partition = _m.group(1)
                            break
                else:
                    partition = ""
                if "cpu" in partition:
                    raise RuntimeError(
                        f"Architecture Mismatch: {receipt.get('name', 'app')} is built for GPU but partition is {partition}"
                    )
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
        allocation = self.exp_opts.get("allocation", {})

        if "partitions" in allocation:
            NodeAllocator.allocate_partitioned(self.apps, self.node_list, allocation)
        else:
            mode = allocation.get("mode", "linear")
            split_val = allocation.get("split", "even")
            split = NodeAllocator.get_abs_split(split_val, len(self.apps), len(self.node_list))
            if mode == "interleaved":
                NodeAllocator.allocate_interleaved(
                    self.apps, self.node_list, split, stride=allocation.get("stride", 1)
                )
            elif mode == "random":
                NodeAllocator.allocate_random(
                    self.apps, self.node_list, split, seed=allocation.get("seed")
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
                        msg_size = int(tokens[tokens.index("-msgsize") + 1])
                    except (ValueError, IndexError):
                        pass

                app.msg_size = msg_size
                self.data_containers.extend(setup_containers(app))

    def execute(self, data_path):
        """Main execution loop (Setup -> Run -> Wait -> Converge)."""
        self.log.info("Execution started")

        # Tracking states initialized before the loop iterations begin
        experiment_status = "COMPLETED"

        # Params
        min_runs = int(self.exp_opts.get("minruns", 10))
        max_runs = int(self.exp_opts.get("maxruns", 20))
        timeout = float(self.exp_opts.get("timeout", 1200.0))
        converge_all = parse_bool(self.exp_opts.get("convergeall", False), "convergeall")
        alpha = float(self.exp_opts.get("alpha", 0.05))
        beta = float(self.exp_opts.get("beta", 0.05))

        # Recupera l'header dalle opzioni globali (dove l'Orchestrator lo ha messo)
        # Default a lista vuota se non esiste. Header is strictly global.
        # Schedule Logic Preparation
        dependency_map = {}
        static_schedule = []
        rel_durations = {}

        # Build Schedule
        for i, app in enumerate(self.apps):
            # Start
            if app.start_string.startswith("s"):
                dependency_map[i] = int(app.start_string[1:])
            else:
                static_schedule.append((i, "s", float(app.start_string)))

            # End
            if app.config_end and app.config_end != "f":
                val = float(app.config_end)
                if app.start_string.startswith("s"):
                    rel_durations[i] = val
                else:
                    static_schedule.append((i, "k", val))

        runs = 0
        failed_runs = 0
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

                # Each app gets its own absolute working directory for this run, so files it
                # writes relative to its cwd never collide with a co-running app's.
                run_root = os.path.abspath(os.path.join(self.exp_dir, f"run_{runs + 1}"))
                for app in self.apps:
                    app.run_dir = os.path.join(run_root, f"app_{app.id_num}")
                    os.makedirs(app.run_dir, exist_ok=True)
                    # An app that does not run (or whose output can't be read) in this run
                    # must not be collected again with the previous run's output.
                    app.process = None
                    app.stdout = None
                    app.stderr = None
                    app.raw_stdout_buffer = []

                # Reset ephemeral schedule for this run
                curr_schedule = sorted(static_schedule, key=lambda x: x[2])
                curr_deps = dependency_map.copy()
                running = set()
                finished = set()

                f_app_ids = {i for i, app in enumerate(self.apps) if str(app.config_end) == "f"}

                # Inner Event Loop
                while True:
                    now = time.time() - run_start

                    # 1. Time-based events
                    while curr_schedule and curr_schedule[0][2] <= now:
                        aid, action, _ = curr_schedule.pop(0)
                        if action == "s":
                            if aid not in running:
                                app_log = run_log.enter(f"App {aid}")
                                concurrent = len(static_schedule) > 1 or len(dependency_map) > 0

                                # --- Merge Hooks and Override Launcher ---
                                merged_pre_commands = self.apps[aid].get_pre_commands()
                                launcher_override = self.apps[aid].get_launcher_override()

                                run_job(
                                    self.apps[aid],
                                    self.wlmanager,
                                    self.ppn,
                                    logger=app_log,
                                    pre_commands=merged_pre_commands,
                                    live_stream=concurrent,
                                    data_path=data_path,
                                    launcher=launcher_override,
                                )

                                running.add(aid)
                        elif action == "k":
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
                                if (
                                    hasattr(self.apps[aid], "_stream_thread")
                                    and self.apps[aid]._stream_thread
                                ):
                                    self.apps[aid]._stream_thread.join(timeout=2.0)

                                # stdout is already consumed by the thread, so communicate() only gets stderr
                                _, err = proc.communicate()

                                # Reconstruct stdout from the buffer
                                out = b"".join(getattr(self.apps[aid], "raw_stdout_buffer", []))

                                self.apps[aid].set_output(out, err)

                                exit_code = proc.returncode
                                if exit_code != 0:
                                    app_log.error(f"FAILED  exit={exit_code}")
                                    run_successful = False

                                    if experiment_status != "TIMEOUT":
                                        experiment_status = "FAILED"

                                    # Extract both streams
                                    stdout_text = (
                                        out.decode("utf-8", errors="replace")
                                        if isinstance(out, bytes)
                                        else out
                                    )
                                    stderr_text = (
                                        err.decode("utf-8", errors="replace")
                                        if isinstance(err, bytes)
                                        else err
                                    )

                                    # Forward BOTH streams to the console if they exist
                                    if stdout_text.strip():
                                        app_log.app_output("STDOUT Dump:", stdout_text)
                                    if stderr_text and stderr_text.strip():
                                        app_log.app_output("STDERR Dump:", stderr_text)

                                    # Write detailed error log to experiment dir, including both streams
                                    try:
                                        err_path = os.path.join(
                                            self.exp_dir, f"error_app_{aid}.log"
                                        )
                                        with open(err_path, "w") as f:
                                            f.write(f"App {aid} exit={exit_code}\n")
                                            if stdout_text.strip():
                                                f.write(f"\n--- STDOUT ---\n{stdout_text}\n")
                                            if stderr_text and stderr_text.strip():
                                                f.write(f"\n--- STDERR ---\n{stderr_text}\n")
                                    except Exception:
                                        app_log.warning("Could not write error log file")
                                else:
                                    app_log.info("FINISHED  exit=0")
                                    # REMOVED: The logic that forwarded 'out' to app_log.app_output
                                    # Data is now silently waiting in self.apps[aid].stdout for the CSV parser.

                            except Exception as e:
                                app_log.error(f"Failed reading output: {e}")
                                run_successful = False
                                if experiment_status != "TIMEOUT":
                                    experiment_status = "FAILED"
                                self.apps[aid].process = None  # nothing readable to collect

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

                            run_job(
                                self.apps[waiter],
                                self.wlmanager,
                                self.ppn,
                                logger=dep_log,
                                pre_commands=merged_pre_commands,
                                live_stream=True,
                                data_path=data_path,
                                launcher=launcher_override,
                            )

                            running.add(waiter)
                            if waiter in rel_durations:
                                curr_schedule.append((waiter, "k", now + rel_durations[waiter]))
                                curr_schedule.sort(key=lambda x: x[2])
                            started_deps.append(waiter)
                    for s in started_deps:
                        del curr_deps[s]

                    if not curr_schedule and not curr_deps and not (running - f_app_ids):
                        break

                    # Check if the global elapsed time has exceeded the timeout
                    if (time.time() - global_start) >= timeout:
                        run_log.error(f"HARD TIMEOUT: Experiment exceeded {timeout}s mid-run.")
                        experiment_status = "TIMEOUT"
                        for active_aid in list(running):
                            try:
                                os.killpg(
                                    os.getpgid(self.apps[active_aid].process.pid), signal.SIGKILL
                                )
                            except OSError:
                                pass
                        break  # Break the inner loop, forcing a teardown

                    time.sleep(0.05)

                # ── Lorenzo's modifications ──────────────────────────────
                # Kill "f" apps now that all other work is done
                for app in self.apps:
                    if str(app.config_end) == "f":
                        if app.process is not None and app.process.poll() is None:
                            end_job(app, run_log)
                # ─────────────────────────────────────────────────────────

                # Remove app directories whose only visible content is the hidden
                # .wrappers/ build artefact (they look empty), then the run directory if
                # nothing is left in it.
                for app in self.apps:
                    _app_dir = getattr(app, "run_dir", None)
                    if _app_dir and os.path.isdir(_app_dir):
                        if not any(f for f in os.listdir(_app_dir) if not f.startswith(".")):
                            shutil.rmtree(_app_dir, ignore_errors=True)
                if os.path.isdir(run_root) and not os.listdir(run_root):
                    os.rmdir(run_root)

                #! Lorenzo's ping: it is better to collect the data while we are polling, or we need to print some [INFO] logs to understand it is running or not
                #! read_data is defined from the wrapper, we need to make it clear
                # Collect Data: a parse failure fails the run like a non-zero exit.
                if not collect_run(self.apps, self.data_containers, run_log, run_id=runs + 1):
                    run_successful = False
                    if experiment_status != "TIMEOUT":
                        experiment_status = "FAILED"

                # Before any cleanup below can remove the run directory.
                copy_artifacts(self.apps, self.exp_dir, runs + 1, run_log)

                # Clean Dirs Policy
                # Default to True for maximum data safety if the flag is missing
                retain_files = parse_bool(self.exp_opts.get("retain_files", True), "retain_files")

                if not retain_files and run_successful and os.path.exists(run_root):
                    # ignore_errors=True prevents transient parallel filesystem locks
                    # from crashing the orchestrator loop
                    shutil.rmtree(run_root, ignore_errors=True)

                runs += 1
                if not run_successful:
                    failed_runs += 1
                if runs >= min_runs:
                    converged = check_CI(self.data_containers, alpha, beta, converge_all, runs)
                    if converged:
                        self.log.info(f"Converged at run {runs}")

        finally:
            self.teardown()

        self._write_to_registry(status=experiment_status, total_runs=runs, failed_runs=failed_runs)

    def teardown(self):
        """Ensures all processes are killed before next experiment."""
        for app in self.apps:
            if hasattr(app, "process") and app.process:
                if app.process.poll() is None:
                    try:
                        os.killpg(os.getpgid(app.process.pid), signal.SIGKILL)
                    except OSError:
                        pass

    def save_results(self):
        """Persists data to disk."""
        if self.data_containers:
            out_fmt = self.exp_opts.get("outformat", "csv")
            prefix = os.path.join(self.exp_dir, "data")
            log_data(out_fmt, prefix, self.data_containers)
            self.log.info(f"Data saved to {self.exp_dir}")

    def _write_to_registry(self, status, total_runs, failed_runs):
        """
        Appends a data row for this experiment to the system-level metadata.csv.
        Uses exclusive POSIX file locking to guarantee process safety on shared HPC filesystems.

        `total_runs`/`failed_runs` let a caller distinguish "this experiment's
        overall status latched to FAILED because of one bad run, but N of M
        runs actually succeeded and have data" from "every run failed" (plan
        081). An existing metadata.csv from before this field existed keeps
        its old header forever (no migration, by design) -- new rows still
        append fine, they just aren't readable by these column names.
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
            unique_apps = sorted(
                list(
                    set(
                        [
                            str(getattr(app, "benchmark_id", getattr(app, "name", "unknown")))
                            for app in self.apps
                        ]
                    )
                )
            )
            apps_list = " ".join(unique_apps)

            tags = self.global_opts.get("tags", "none")
            relative_path = f"./{job_basename}/{exp_basename}"

            headers = [
                "job_name",
                "experiment_name",
                "timestamp",
                "numnodes",
                "ppn",
                "apps_list",
                "status",
                "tags",
                "relative_path",
                "total_runs",
                "failed_runs",
            ]
            row = [
                job_name,
                exp_basename,
                timestamp,
                numnodes,
                ppn,
                apps_list,
                status,
                tags,
                relative_path,
                total_runs,
                failed_runs,
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
