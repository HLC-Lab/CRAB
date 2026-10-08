from __future__ import annotations

import datetime
import json
import os
import re
import time
from typing import Any

from crab.core.config_checks import check_config
from crab.core.execution.scheduler.select import scheduler_for
from crab.core.execution.settings import SLURM_DEFAULT, ExecutionSettings, to_json
from crab.core.experiment import ExperimentRunner
from crab.core.provenance import write_provenance
from crab.log import CrabLogger

CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Version of the config.json format this engine reads. A config without the field predates it
# and is version 1. Bump only with a migration for the previous version.
CONFIG_SCHEMA_VERSION = 2


def check_config_schema_version(config: dict[str, Any]) -> None:
    """Refuse a config written for a newer or unknown config format.

    Raises:
        ValueError: if `schema_version` is present but not an integer >= 1, or newer than this engine.
    """
    if "schema_version" not in config:
        return
    version = config["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"schema_version must be an integer of at least 1, got {version!r}")
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"This config has schema_version {version}, but this CRAB reads up to "
            f"{CONFIG_SCHEMA_VERSION}. Run `crab update` on this machine."
        )


class Engine:
    def __init__(self, logger: CrabLogger):
        self.log = logger

    def run(
        self,
        config: dict[str, Any],
        environment: dict[str, Any],
        is_worker: bool = False,
        output_dir: str = None,
        only: list[str] | None = None,
        settings: ExecutionSettings | None = None,
    ):
        check_config_schema_version(config)
        settings = settings or SLURM_DEFAULT
        for warning in check_config(config, settings):
            self.log.warning(warning)
        if is_worker:
            return self._run_worker(config, environment, output_dir, settings=settings)
        else:
            return self._run_orchestrator(config, environment, only=only, settings=settings)

    def _run_orchestrator(
        self,
        config: dict[str, Any],
        environment: dict[str, Any],
        only: list[str] | None = None,
        settings: ExecutionSettings = SLURM_DEFAULT,
    ):
        self.log.info("Engine running in ORCHESTRATOR mode")

        if "experiments" not in config:
            if "applications" in config:
                apps_data = config.pop("applications")
                if isinstance(apps_data, dict) and "apps" in apps_data:
                    # TUI format: {"apps": {...}, "local_options": {...}}
                    config["experiments"] = {"default_ex": apps_data}
                else:
                    # Legacy flat format: {0: {...}, 1: {...}}
                    config["experiments"] = {"default_ex": {"apps": apps_data}}
            else:
                raise ValueError("Config must contain 'experiments' or 'applications'.")

        if only is not None:
            unknown = sorted(set(only) - set(config["experiments"].keys()))
            if unknown:
                raise ValueError(f"Unknown experiment key(s) in --only: {unknown}")
            config["experiments"] = {k: v for k, v in config["experiments"].items() if k in only}

        g_opts = config.get("global_options", {})
        data_path = g_opts.get("datapath", os.path.join(CRAB_ROOT, "data"))
        if g_opts.get("numnodes") is None:
            raise ValueError("global_options.numnodes is required in the config file")

        os.makedirs(data_path, exist_ok=True)

        # 1. Genera timestamp base
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")

        # 2. Cerca il nome custom nelle opzioni
        custom_name = g_opts.get("name", "")

        if custom_name:
            # Sanificazione: mantieni solo alfanumerici, trattini e underscore
            # Sostituisci spazi o altri caratteri con '_'
            safe_name = "".join(
                [c if c.isalnum() or c in ("-", "_") else "_" for c in str(custom_name)]
            )
            # Formato: NAME_TIMESTAMP
            folder_name = f"{safe_name}_{timestamp_str}"
        else:
            # Fallback legacy: solo TIMESTAMP
            folder_name = timestamp_str

        # 3. Costruzione path finale — sanitize CRAB_SYSTEM to prevent path traversal
        raw_system = str(environment.get("CRAB_SYSTEM", "unknown"))
        safe_system = re.sub(r"[^\w\-]", "_", raw_system)
        runner_id = safe_system + "/" + folder_name
        data_directory = os.path.join(data_path, runner_id)
        # --------------------------------------

        os.makedirs(data_directory, exist_ok=True)

        with open(os.path.join(data_directory, "config.json"), "w") as f:
            json.dump(config, f, indent=4)
        with open(os.path.join(data_directory, "environment.json"), "w") as f:
            json.dump(environment, f, indent=4)
        with open(os.path.join(data_directory, "execution.json"), "w") as f:
            json.dump(to_json(settings), f, indent=4)

        job_id = scheduler_for(settings.scheduler, self.log, CRAB_ROOT).submit(
            data_directory, g_opts
        )

        # Structured result for programmatic callers (e.g. `crab run --json`).
        return {
            "job_id": job_id,
            "data_dir": data_directory,
            "system": safe_system,
        }

    def _run_worker(
        self,
        config: dict[str, Any],
        environment: dict[str, Any],
        output_dir: str,
        settings: ExecutionSettings = SLURM_DEFAULT,
    ):
        self.log.info("Worker started")

        orig_env = os.environ.copy()

        # Expand all values against the original env snapshot before any mutation,
        # so that keys within `environment` do not cross-pollinate each other's expansions.
        expanded = {k: os.path.expandvars(str(v)) for k, v in environment.items()}
        os.environ.update(expanded)

        try:
            full_node_list = scheduler_for(settings.scheduler, self.log, CRAB_ROOT).node_list()
            self.log.info(f"Allocated {len(full_node_list)} node(s)")
            write_provenance(output_dir, config, full_node_list)

            global_opts = config.get("global_options", {})
            experiments = config.get("experiments", {})
            sorted_exp_ids = sorted(experiments.keys())
            total_exps = len(sorted_exp_ids)

            failed: list[str] = []
            for idx, exp_id in enumerate(sorted_exp_ids, 1):
                exp_config = experiments[exp_id]
                self.log.info(f"Starting experiment [{idx}/{total_exps}]: {exp_id}")

                runner = ExperimentRunner(
                    exp_name=exp_id,
                    config=exp_config,
                    global_options=global_opts,
                    node_list=full_node_list,
                    output_dir=output_dir,
                    logger=self.log,
                    settings=settings,
                )
                try:
                    runner.setup()
                    runner.execute(output_dir)
                    runner.save_results()
                except Exception as e:
                    self.log.error(f"Experiment {exp_id} failed: {e}")
                    import traceback

                    traceback.print_exc()
                    failed.append(exp_id)
                finally:
                    runner.teardown()
                    time.sleep(2)

            self.log.info("All experiments finished")
            # The other experiments still ran; the job itself must end failed (its exit status
            # is the Slurm job state and the local job's local_exit_code).
            if failed:
                raise RuntimeError(
                    f"{len(failed)} of {total_exps} experiments failed: {', '.join(failed)}"
                )

        finally:
            os.environ.clear()
            os.environ.update(orig_env)
