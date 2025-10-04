import argparse
import json
import os
import sys
from typing import Dict, Any

# Aggiungi 'src' al path di sistema PRIMA di qualsiasi altro import custom
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from crab.core.engine import Engine

def load_environment_config(preset_arg: str) -> Dict[str, Any]:
    presets_filename = "presets.json"
    print(f"Info: Loading preset '{preset_arg}' from {presets_filename}", flush=True)
    try:
        with open(presets_filename, 'r') as f:
            all_presets = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"The presets file '{presets_filename}' was not found.")

    if preset_arg not in all_presets:
        raise KeyError(f"The preset '{preset_arg}' was not found in {presets_filename}.")

    base_env = all_presets.get("_common", {})
    preset_env = all_presets[preset_arg]
    env_config = {**base_env, **preset_env}

    if "BLINK_SYSTEM" not in env_config:
        env_config["BLINK_SYSTEM"] = preset_arg
    return env_config

def prepare_execution_environment(env_config: Dict[str, Any]) -> Dict[str, str]:
    execution_env = os.environ.copy()
    processed_config = {}
    for key, value in env_config.items():
        if isinstance(value, str):
            value = value.replace("__CWD__", os.getcwd())
        processed_config[key] = str(value)
    execution_env.update(processed_config)
    final_env = {}
    for key, value in execution_env.items():
        final_env[key] = os.path.expandvars(value)
    return final_env

def run_from_cli():
    # --- LOGICA DI DISTINZIONE TRA ORCHESTRATORE E WORKER ---
    if "--worker" in sys.argv:
        try:
            # Estrai il percorso della directory di lavoro dall'argomento --workdir
            workdir_index = sys.argv.index("--workdir") + 1
            work_dir = sys.argv[workdir_index]

            config_file = os.path.join(work_dir, 'config.json')
            env_file = os.path.join(work_dir, 'environment.json')

            print(f"--- [WORKER MODE DETECTED] Work dir: {work_dir} ---", flush=True)

            with open(config_file, 'r') as f:
                benchmark_config = json.load(f)
            
            with open(env_file, 'r') as f:
                execution_env = json.load(f)
            
            print(f"--- [WORKER] Environment loaded. Starting engine. ---", flush=True)

            engine = Engine(log_callback=print)
            engine.run(
                config=benchmark_config, 
                environment=execution_env,
                is_worker=True,
                output_dir=work_dir
            )
            print(f"--- [WORKER] Engine run finished. ---", flush=True)

        except Exception as e:
            print(f"[WORKER FATAL ERROR] {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # --- LOGICA DELL'ORCHESTRATORE ---
        parser = argparse.ArgumentParser(description="CRAB Benchmarking Orchestrator.")
        parser.add_argument("-c", "--config", dest="app_config_file", required=True, help="Path to the JSON benchmark config.")
        parser.add_argument("-p", "--preset", help="Name of the preset to use (e.g., leonardo).")
        # Aggiungiamo il flag worker anche qui per evitare errori di parsing
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        args = parser.parse_args()

        try:
            selected_preset = args.preset or os.environ.get("BLINK_PRESET") or "local"
            if os.path.exists(".env") and not args.preset:
                with open(".env", "r") as f:
                    selected_preset = f.read().strip()

            env_config = load_environment_config(selected_preset)
            execution_env = prepare_execution_environment(env_config)
            with open(args.app_config_file, 'r') as f:
                benchmark_config = json.load(f)

            print("-" * 50)
            print(f"Avvio del motore con il preset '{selected_preset}'...")
            print("-" * 50)

            engine = Engine(log_callback=print)
            engine.run(
                config=benchmark_config, 
                environment=execution_env,
                is_worker=args.worker
            )

            print("-" * 50)
            print("Orchestration complete. Job submitted to SLURM.")
            print("-" * 50)

        except Exception as e:
            print(f"[ORCHESTRATOR FATAL ERROR] {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
