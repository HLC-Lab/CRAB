# app/core/benchmark_runner.py
import subprocess
import threading
import os
from typing import Callable, Dict

# Usa i tuoi modelli di dati esistenti
from .models import BenchmarkState

LogCallback = Callable[[str], None]

class BlinkRunner:
    def __init__(self, state: BenchmarkState, log_callback: LogCallback):
        self.state = state
        self.log = log_callback

    def _execute_benchmark_logic(self, tui_settings: Dict[str, str], selected_preset: str):
        self.log("[bold blue]Preparing to run benchmark...[/]")

        # --- 1. Preparazione dell'Ambiente di Esecuzione ---
        execution_env = os.environ.copy()
        
        # NOTA: `tui_settings` ora è un argomento, quindi non serve più prenderlo da `app_ref`

        # Aggiungi BLINK_SYSTEM dinamicamente
        if selected_preset != "Custom":
            tui_settings["BLINK_SYSTEM"] = selected_preset

        # Sostituisci placeholder come __CWD__
        for key, value in tui_settings.items():
            if isinstance(value, str) and value == "__CWD__":
                tui_settings[key] = os.getcwd() + "/"

        # Applica le impostazioni della TUI all'ambiente
        self.log("Applying TUI environment settings...")
        execution_env.update(tui_settings)

        # Esegui l'espansione delle variabili (es. $PATH)
        for key, value in execution_env.items():
            if isinstance(value, str):
                execution_env[key] = os.path.expandvars(value)

        # --- Fine Preparazione Ambiente ---

        self.log("Generating benchmark configuration...")

        app_mix_path = "benchmark_mix.txt"
        try:
            with open(app_mix_path, "w") as f:
                f.write(",\n")
                # USA I DATI DALLO STATO DELLA CLASSE
                sorted_benchmarks = sorted(self.state.apps.items())
                for _, config in sorted_benchmarks:
                    path = config.path
                    args = config.args
                    collect = "1" if config.collect else "0"
                    start = config.start or "0"
                    end = config.end or "f"
                    f.write(f"{path},{args},{collect},{start},{end}\n")
            self.log(f"Successfully created '{app_mix_path}'\n")
        except Exception as e:
            self.log(f"[bold red]Error generating config file: {e}[/]")
            return

        # Create a temporary node file
        node_file_path = "tui_node_file"
        with open(node_file_path, "w") as f:
            f.write("localhost\n")

        # Checks if it's using SLURM 
        nodes = "auto" if execution_env.get("BLINK_WL_MANAGER") == "slurm" else node_file_path

        # command = [
        #     "python3", "-u", "blink_core.py", app_mix_path, nodes,
        #     "-n", "1", "-am", "l", "-mn", "1", "-mx", "2",
        #     "-t", "600", "-p", "2", "-ro", "stdout"
        # ]

        command = [
            "python", "-u", "blink_core.py", "prova6.json"
        ]

        self.log(f"[bold red]Starting benchmark...[/]")
        self.log(f"Command: {' '.join(command)}")

        try:
            process = subprocess.Popen(
                command, env=execution_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True
            )

            # Qui la magia: basta chiamare self.log, che è la callback
            for line in iter(process.stdout.readline, ''):
                if line: # Assicurati che la linea non sia vuota
                    self.log(line.strip())

            process.wait()
            self.log(f"\n[bold green]Benchmark finished with exit code: {process.returncode}[/]")

        except FileNotFoundError:
            self.log("[bold red]Error: 'blink_core.py' not found.[/]")
        except Exception as e:
            self.log(f"[bold red]An error occurred: {e}[/]")

    def run_in_thread(self, tui_settings: Dict[str, str], selected_preset: str):
        # Questo metodo avvia il thread, che è l'unica cosa che farà
        thread = threading.Thread(
            target=self._execute_benchmark_logic,
            args=(tui_settings, selected_preset) # Passiamo gli argomenti al nostro worker
        )
        thread.start()
