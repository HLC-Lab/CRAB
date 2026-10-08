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
from .artifacts import copy_artifacts
from .registry import write_registry_row
from .schedule import build_schedule, kill_f_apps, run_events


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

            # Find the binary now so a missing one fails the experiment before any run
            # starts. A wrapper that builds its own command (QE) is checked when it names a
            # binary source; one that names none may not launch a binary at all.
            launches_via_base = type(app_instance).run_app is base.run_app
            names_a_binary = bool(app_instance.benchmark_id or app_instance.executable)
            if launches_via_base or names_a_binary:
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
        NodeAllocator.allocate_experiment(self.apps, self.node_list, allocation)

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
        schedule = build_schedule(self.apps)

        runs = 0
        failed_runs = 0
        global_start = time.time()
        converged = False
        run_open = False  # a run has started and is not counted yet

        try:
            while True:
                # Exit conditions
                elapsed = time.time() - global_start
                if runs >= max_runs or (runs >= min_runs and converged) or elapsed >= timeout:
                    break

                run_log = self.log.enter(f"Run {runs + 1}")
                run_log.info("Started")
                run_open = True

                run_start = time.time()

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

                outcome = run_events(
                    self.apps,
                    self.wlmanager,
                    self.ppn,
                    schedule,
                    run_log,
                    data_path,
                    self.exp_dir,
                    timeout,
                    global_start,
                    run_start,
                    experiment_status,
                )
                run_successful = outcome.run_successful
                experiment_status = outcome.experiment_status

                kill_f_apps(self.apps, run_log)

                #! Lorenzo's ping: it is better to collect the data while we are polling, or we need to print some [INFO] logs to understand it is running or not
                #! read_data is defined from the wrapper, we need to make it clear
                # Collect Data: a parse failure fails the run like a non-zero exit.
                if not collect_run(self.apps, self.data_containers, run_log, run_id=runs + 1):
                    run_successful = False
                    if experiment_status != "TIMEOUT":
                        experiment_status = "FAILED"

                # Before any cleanup below can remove the run directory.
                copy_artifacts(self.apps, self.exp_dir, runs + 1, run_log)

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
                run_open = False
                if runs >= min_runs:
                    converged = check_CI(self.data_containers, alpha, beta, converge_all, runs)
                    if converged:
                        self.log.info(f"Converged at run {runs}")

        except Exception as e:
            # Keep what the completed runs measured; the run that raised counts as failed.
            completed_runs = runs
            if run_open:
                runs += 1
                failed_runs += 1
            self.log.error(f"Experiment stopped in run {runs}: {e}")
            if completed_runs:
                try:
                    self.save_results()
                except Exception as save_error:
                    self.log.error(f"Could not save the completed runs: {save_error}")
            self._write_to_registry(status="FAILED", total_runs=runs, failed_runs=failed_runs)
            raise
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
        """Appends a data row for this experiment to the system-level metadata.csv."""
        # Target ppn across common layout dictionary keys
        ppn = getattr(self, "ppn", "unknown")
        write_registry_row(
            self.exp_dir,
            self.global_opts,
            ppn,
            self.apps,
            status,
            total_runs,
            failed_runs,
            lambda message: self.log.error(message),
        )
