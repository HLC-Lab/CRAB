import argparse
import json
import os
import sys
from typing import Dict, Any

from ..core.engine import Engine # Importa il nuovo motore

def load_environment_config(preset_arg: str) -> Dict[str, Any]:
    presets_filename = "presets.json"
    print(f"Info: Loading preset '{preset_arg}' from {presets_filename}")
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
    # Aggiungi 'src' al path di sistema per permettere gli import
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))




    parser = argparse.ArgumentParser(
        description="CRAB Benchmarking Orchestrator.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    # Rinominiamo 'app_config_file' a '--config' per coerenza
    parser.add_argument(
        "-c", "--config",
        dest="app_config_file",
        required=True,
        help="Path to the JSON configuration file for the benchmark."
    )
    
    # Aggiungiamo il flag per la modalità worker
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Internal flag: run the engine in worker mode inside a SLURM allocation."
    )

    try:
        with open('presets.json', 'r') as f:
            presets = json.load(f)
        available_presets = [k for k in presets if k != '_common']
        help_text = f"Name of the preset to use. Can be specified via .env file." + "Available presets: {'\n - '.join(['', *available_presets])}"
    except FileNotFoundError:
        help_text = "Name of the preset to use (presets.json not found)."
    parser.add_argument("-p", "--preset", help=help_text)

    args = parser.parse_args()

    try:
        # 1. Checks if .env file exists and loads it
        # (questa parte rimane uguale)
        selected_preset = ""
        if os.path.exists(".env") and not args.preset:
            try:
                with open(".env", "r") as f:
                    selected_preset = f.read().strip()
            except Exception as e:
                print(f"Could not read .env file: {e}") # Usiamo print qui
        else:
            if not args.preset:
                selected_preset = "local"
            else:
                selected_preset = args.preset

        # 2. Carica la configurazione dell'ambiente
        env_config = load_environment_config(selected_preset)

        # 3. Prepara l'ambiente di esecuzione
        execution_env = prepare_execution_environment(env_config)

        # 4. Carica la configurazione del benchmark dal file JSON
        with open(args.app_config_file, 'r') as f:
            benchmark_config = json.load(f)

        # 5. Istanzia ed esegui il motore, passando il flag 'worker'
        print("-" * 50)
        print(f"Avvio del motore con il preset '{selected_preset}'...")
        print("-" * 50)

        engine = Engine(log_callback=print)
        engine.run(
            config=benchmark_config, 
            environment=execution_env,
            is_worker=args.worker  # <-- PASSAGGIO CHIAVE
        )

        print("-" * 50)
        print("Benchmark terminato con successo.")
        print("-" * 50)

    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"[Errore] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[Errore] Si è verificato un errore imprevisto nel motore: {e}", file=sys.stderr)
        sys.exit(1)

