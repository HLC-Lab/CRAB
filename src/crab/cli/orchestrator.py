import json
import os
import sys
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crab.log import LogLevel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Walk up: cli -> crab -> src -> CRAB_ROOT
CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import crab.setup.memory as memory  # noqa: E402 -- must follow the sys.path setup above


def load_environment_config(preset_arg: str) -> dict[str, Any]:
    presets_filename = os.path.join(CRAB_ROOT, "config", "presets.json")
    try:
        with open(presets_filename) as f:
            all_presets = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"The presets file '{presets_filename}' was not found.") from None

    if preset_arg not in all_presets:
        raise KeyError(f"The preset '{preset_arg}' was not found in {presets_filename}.")

    # Carica _common e il preset specifico
    common_preset = all_presets.get("_common", {})
    target_preset = all_presets[preset_arg]

    # 1. Merge Environment Variables (Dict update)
    final_env = common_preset.get("env", {}).copy()
    final_env.update(target_preset.get("env", {}))

    # Assicuriamo che CRAB_SYSTEM sia impostato
    if "CRAB_SYSTEM" not in final_env:
        final_env["CRAB_SYSTEM"] = preset_arg

    # 2. Merge SBATCH directives (List extend)
    # L'ordine è: Common -> Preset. (Engine poi aggiungerà Experiment overrides)
    final_sbatch = common_preset.get("sbatch", []) + target_preset.get("sbatch", [])

    # 3. Merge Header commands (List extend)
    final_header = common_preset.get("header", []) + target_preset.get("header", [])

    # Restituiamo una struttura configurata completa
    return {"env": final_env, "sbatch": final_sbatch, "header": final_header}


def _parse_log_level(raw: str) -> "LogLevel":
    """Convert a CLI string to a LogLevel, defaulting to INFO."""
    from crab.log import LogLevel

    mapping = {
        "DEBUG": LogLevel.DEBUG,
        "INFO": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
        "ERROR": LogLevel.ERROR,
        "CRITICAL": LogLevel.CRITICAL,
    }
    return mapping.get(raw.upper().strip(), LogLevel.INFO)


def prepare_execution_environment(env_dict: dict[str, Any]) -> dict[str, str]:
    """
    Builds a clean dictionary of framework-specific variables.
    Does NOT copy the system environment. Does NOT expand bash variables yet.
    """
    processed_env = {}
    for key, value in env_dict.items():
        if isinstance(value, str):
            value = value.replace("__CWD__", CRAB_ROOT)
        processed_env[key] = str(value)
    return processed_env


def execute_worker(work_dir: str, log_level_str: str = None):
    """Executes the worker logic directly from provided arguments."""
    from crab.log import get_logger

    level = _parse_log_level(log_level_str) if log_level_str else None
    logger = get_logger(level=level)

    try:
        config_file = os.path.join(work_dir, "config.json")
        env_file = os.path.join(work_dir, "environment.json")

        logger.info(f"Worker mode detected  workdir={work_dir}")

        with open(config_file) as f:
            benchmark_config = json.load(f)

        with open(env_file) as f:
            execution_env = json.load(f)

        # Resolve the __CWD__ placeholder (presets.json uses it for CRAB_ROOT) that the
        # orchestrator path substitutes but the worker path historically skipped. Without
        # this, CRAB_ROOT reaches the engine as the literal string "__CWD__" and every
        # wrapper that builds paths off os.environ["CRAB_ROOT"] silently breaks. This is
        # what lets the worker run inside an externally-obtained (e.g. SbatchMan) allocation
        # from a hand-written environment.json.
        execution_env = prepare_execution_environment(execution_env)

        logger.info("Environment loaded, starting engine")

        start = time.time()

        from crab.core.engine import Engine

        engine = Engine(logger=logger)
        engine.run(
            config=benchmark_config,
            environment=execution_env,
            is_worker=True,
            output_dir=work_dir,
        )

        elapsed_time = time.time() - start
        total = timedelta(seconds=int(elapsed_time))

        logger.info(f"Engine run finished  elapsed={total}")

    except KeyboardInterrupt:
        logger.warning("Worker interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"Worker fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def execute_orchestrator(
    app_config_file: str,
    preset_arg: str = None,
    log_level_str: str = None,
    as_json: bool = False,
    only: list[str] | None = None,
):
    """Executes the orchestrator logic directly from provided arguments.

    When ``as_json`` is True, all logs are routed to stderr and a single JSON
    object ``{job_id, data_dir, system}`` is printed to stdout, so the web
    backend gets a clean, parseable submit result. ``only`` reruns just the
    given experiment key(s) instead of the whole config (plan 060).
    """
    from crab.log import get_logger

    level = _parse_log_level(log_level_str) if log_level_str else None
    if as_json:
        # Keep stdout clean for the JSON result; logs go to stderr.
        from crab.log import CrabLogger, LogLevel
        from crab.log.formatters import PlainFormatter
        from crab.log.handlers import StreamHandler

        handler = StreamHandler(PlainFormatter(), stream=sys.stderr)
        logger = CrabLogger(level=level or LogLevel.INFO, handlers=[handler])
    else:
        logger = get_logger(level=level)

    try:
        selected_preset = preset_arg or os.environ.get("CRAB_PRESET")
        if os.path.exists(".env") and not selected_preset:
            with open(".env") as f:
                selected_preset = f.read().strip()

        if not selected_preset:
            selected_preset = "local"

        logger.info(f"Loading preset '{selected_preset}'")

        preset_config = load_environment_config(selected_preset)
        execution_env = prepare_execution_environment(preset_config["env"])
        all_receipts = memory.get_all_receipts()

        for bench_id, receipt in all_receipts.items():
            # Injecting into the environment for backward compatibility
            # with any legacy wrappers that haven't updated to use get_receipt()
            env_key = f"CRAB_PATH_{bench_id.upper()}"
            execution_env[env_key] = receipt.get("binary_path", "")

        with open(app_config_file) as f:
            benchmark_config = json.load(f)

        if "global_options" not in benchmark_config:
            benchmark_config["global_options"] = {}

        # Only inject the system preset if the user didn't provide their own overrides in the JSON
        if "system_sbatch" not in benchmark_config["global_options"]:
            benchmark_config["global_options"]["system_sbatch"] = preset_config["sbatch"]

        if "system_header" not in benchmark_config["global_options"]:
            benchmark_config["global_options"]["system_header"] = preset_config["header"]

        logger.info(f"Starting engine with preset '{selected_preset}'")

        from crab.core.engine import Engine

        engine = Engine(logger=logger)
        result = engine.run(
            config=benchmark_config, environment=execution_env, is_worker=False, only=only
        )

        logger.info("Orchestration complete — job submitted to SLURM")

        if as_json:
            print(json.dumps(result or {}, indent=2))

        return result

    except KeyboardInterrupt:
        logger.warning("Orchestrator interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"Orchestrator fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
