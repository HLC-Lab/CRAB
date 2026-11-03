"""Loading the apps of one experiment from its config.

`load_apps` is the first step of `ExperimentRunner.setup`: it finds each app's wrapper, instantiates
it, injects the config keys, resolves its binary and checks the GPU architecture guard. It lives
apart from the runner so the runner stays under the size cap; it takes plain inputs and knows
nothing about the runner object.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from crab.core.config_checks import parse_bool
from crab.core.execution.placement import app_ppn
from crab.core.wrapper_paths import (
    load_module,
    missing_wrappers_hint,
    moved_wrapper_note,
    resolve_wrapper_path,
    wrapper_path_problem,
)
from crab.log import CrabLogger
from crab.wrappers.base import base


@dataclass(frozen=True)
class LoadedApps:
    """What `setup` needs about the apps once they are loaded, one entry per placed app."""

    wrapper_paths: list[str]
    placed_keys: list[str]  # the config key each app has in `apps`
    placed_configs: list[dict[str, Any]]  # each app's own config dict


def load_apps(
    app_configs: dict[str, Any],
    exp_opts: dict[str, Any],
    global_opts: dict[str, Any],
    log: CrabLogger,
    apps: list[Any],
) -> LoadedApps:
    """Instantiate the app of every config entry that has a `path`, in key order.

    Args:
        app_configs: the experiment's `apps` mapping (config key to the app's config dict).
        exp_opts: the global options with the experiment's local options merged over them.
        global_opts: the global options alone (the architecture guard reads `sbatch_directives`
            from these).
        log: the experiment's logger.
        apps: filled with each app instance as it is loaded, so an app that raises leaves the
            earlier ones in the caller's list (`ExperimentRunner.teardown` walks it).

    Raises:
        FileNotFoundError: a wrapper path does not resolve or does not exist.
        RuntimeError: a GPU-built app is placed on a CPU partition.
    """
    sorted_keys = sorted(app_configs.keys(), key=lambda x: int(x) if x.isdigit() else x)

    # App Instantiation
    idx_counter = 0
    wrapper_paths: list[str] = []
    placed_keys: list[str] = []
    placed_configs: list[dict[str, Any]] = []
    for key in sorted_keys:
        details = app_configs[key]
        config_key = str(key)  # `key` is reused below
        path = details.get("path")
        if not path:
            continue

        moved_note = moved_wrapper_note(path)
        if moved_note:
            log.warning(moved_note)
        config_path = path
        resolved = resolve_wrapper_path(path)
        if resolved is None:
            # `crab worker` reaches this point without the orchestrator's check.
            problem = wrapper_path_problem(config_path)
            log.error(problem)
            raise FileNotFoundError(problem)
        path = resolved

        if not os.path.exists(path):
            log.error(f"Wrapper not found at: {path}")
            hint = missing_wrappers_hint()
            raise FileNotFoundError(f"Wrapper not found: {path}" + (f". {hint}" if hint else ""))

        # Load App Class
        mod_app = load_module(path)
        args = details.get("args", "")
        collect = parse_bool(details.get("collect", False), "collect")

        # Instantiate the app
        app_instance = mod_app.app(idx_counter, collect, args)

        # --- DYNAMIC CONFIG INJECTION ---
        # Any key in the JSON 'apps' config that isn't a reserved CRAB keyword
        # gets injected as an attribute into the app instance.
        reserved_keys = [
            "path",
            "args",
            "collect",
            "start",
            "end",
            "partition",
            "cpus",
            "env",
            "ppn",
            "nics",
            "gpus",
            "bind",
        ]
        for key, value in details.items():
            if key not in reserved_keys:
                setattr(app_instance, key, value)
        app_instance.ppn = app_ppn(details, exp_opts)
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
            sd = global_opts.get("sbatch_directives", {})
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
        app_instance.config_key = config_key  # the key crab_run.json uses for this app

        apps.append(app_instance)
        wrapper_paths.append(path)
        placed_keys.append(config_key)
        placed_configs.append(details)
        idx_counter += 1

    return LoadedApps(wrapper_paths, placed_keys, placed_configs)
