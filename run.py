# run.py
import argparse
import json
import os
import subprocess
import sys
from typing import Dict, Any

def load_environment_config(preset_arg: str) -> Dict[str, Any]:
    """
    Loads the environment configuration from a preset name.

    The function looks for a file named 'presets.json' in the current directory,
    finds the specified preset, and merges it with the '_common' configuration.

    Args:
        preset_arg: The name of the preset to load (e.g., "lumi", "local").

    Returns:
        A dictionary containing the environment variables to set.

    Raises:
        FileNotFoundError: If the 'presets.json' file is not found.
        KeyError: If the requested 'preset_arg' does not exist in 'presets.json'.
    """
    presets_filename = "presets.json"
    print(f"Info: Loading preset '{preset_arg}' from {presets_filename}")
    
    try:
        with open(presets_filename, 'r') as f:
            all_presets = json.load(f)
    except FileNotFoundError:
        # Raise an exception that the caller will have to handle.
        raise FileNotFoundError(f"The presets file '{presets_filename}' was not found.")

    # Check if the preset exists in the file.
    if preset_arg not in all_presets:
        # Raise an exception to indicate that the preset is not valid.
        raise KeyError(f"The preset '{preset_arg}' was not found in {presets_filename}.")

    # Merge the common variables ('_common') with the specific preset variables.
    base_env = all_presets.get("_common", {})
    preset_env = all_presets[preset_arg] # Now we use direct access, which is safer after the check.
    
    env_config = {**base_env, **preset_env}

    # Set BLINK_SYSTEM with the preset name if not already explicitly defined.
    if "BLINK_SYSTEM" not in env_config:
        env_config["BLINK_SYSTEM"] = preset_arg

    return env_config


def prepare_execution_environment(env_config: Dict[str, Any]) -> Dict[str, str]:
    """
    Prepara il dizionario completo dell'ambiente di esecuzione.

    Args:
        env_config: Il dizionario delle variabili d'ambiente specifiche del benchmark.

    Returns:
        Un dizionario dell'ambiente pronto per essere passato a subprocess.
    """
    # Parti dall'ambiente corrente per ereditare variabili come PATH
    execution_env = os.environ.copy()

    # Sostituisci i placeholder e aggiorna l'ambiente
    processed_config = {}
    for key, value in env_config.items():
        if isinstance(value, str):
            # Sostituisci placeholder custom come __CWD__
            value = value.replace("__CWD__", os.getcwd())
        processed_config[key] = str(value) # Assicurati che tutti i valori siano stringhe
    
    execution_env.update(processed_config)

    # Esegui l'espansione delle variabili (es. $HOME, ${LD_LIBRARY_PATH})
    # Questo va fatto dopo che tutte le variabili sono state aggiunte
    final_env = {}
    for key, value in execution_env.items():
        final_env[key] = os.path.expandvars(value)

    return final_env


def main():
    parser = argparse.ArgumentParser(
        description="Entry point for running benchmarks via CLI.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "app_config_file",
        help="Path to the JSON configuration file for applications and global options.\n" +
                "It can override environment variables, see README.md for details."
    )

    # Loads available presets for help text
    try:
        with open('presets.json', 'r') as f:
            presets = json.load(f)
        available_presets = [k for k in presets if k != '_common']
        help_text = f"Name of the preset to use. Available presets: {'\n - '.join(['', *available_presets])}"

    except FileNotFoundError:
        help_text = "Name of the preset to use (presets.json not found)."

    parser.add_argument("-p", "--preset", default="local", help=help_text)

    args = parser.parse_args()

    # 1. Carica la configurazione dell'ambiente
    env_config = load_environment_config(args.preset)

    # 2. Prepara l'ambiente di esecuzione completo
    execution_env = prepare_execution_environment(env_config)

    # 3. Costruisci ed esegui il comando per blink_core.py
    command = ["python", "-u", "blink_core.py", args.app_config_file]
    
    print("-" * 50)
    print(f"Avvio di blink_core.py con il preset '{args.preset}'...")
    print(f"Comando: {' '.join(command)}")
    print("-" * 50)

    try:
        # Avvia il sottoprocesso con l'ambiente personalizzato
        process = subprocess.Popen(
            command,
            env=execution_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # Stampa l'output in tempo reale
        for line in iter(process.stdout.readline, ''):
            if line:
                sys.stdout.write(line)
        
        process.wait()
        print("-" * 50)
        print(f"Benchmark terminato con codice di uscita: {process.returncode}")
        print("-" * 50)

    except FileNotFoundError:
        print(f"[Errore] Comando 'python' o 'blink_core.py' non trovato. Assicurati che siano nel tuo PATH.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[Errore] Si è verificato un errore imprevisto: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
