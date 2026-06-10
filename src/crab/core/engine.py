import datetime
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

from crab.log import CrabLogger
from crab.core.experiment import ExperimentRunner
import pandas

CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

class Engine:
    def __init__(self, logger: CrabLogger):
        self.log = logger

    def run(self, config: Dict[str, Any], environment: Dict[str, Any], is_worker: bool = False, output_dir: str = None):
        if is_worker:
            self._run_worker(config, environment, output_dir)
        else:
            self._run_orchestrator(config, environment)

    def _generate_sbatch_header(self, global_opts: Dict[str, Any], data_directory: str) -> List[str]:
        """
        Generates the list of #SBATCH lines handling defaults, overrides, and security.
        """
        # 1. Definizione dei Parametri Protetti (Il framework vince sempre)
        # Mappa: Chiave -> Valore calcolato dal framework
        protected_defaults = {
            'nodes': f"--nodes={global_opts.get('numnodes')}",
            'ntasks-per-node': f"--ntasks-per-node={global_opts.get('ppn', 1)}",
            # Alias comuni da bloccare
            'N': None, 
            'n': None, # Blocchiamo -n per sicurezza se l'utente prova a passarlo
        }

        # 2. Definizione dei Default Sovrascrivibili
        # Mappa: Chiave univoca -> Stringa completa direttiva
        raw_info = str(global_opts.get('extrainfo', 'job'))
        safe_info = "".join([c if c.isalnum() else '_' for c in raw_info])[:10]
        
        directives_map = {
            'job-name': f"--job-name=crab_{safe_info}",
            'output': f"--output={os.path.join(data_directory, 'slurm_output.log')}",
            'error': f"--error={os.path.join(data_directory, 'slurm_error.log')}",
            'time': f"--time={global_opts.get('walltime', '00:10:00')}"
        }

        # Uniamo i protetti alla mappa (per averli come base)
        for k, v in protected_defaults.items():
            if v: directives_map[k] = v


        # Recuero i default di sistema passati dall'Orchestrator
        system_defaults = global_opts.get('system_sbatch', [])

        # Parsiamo prima i system defaults (bassa priorità rispetto all'utente, alta rispetto ai base)
        for raw in system_defaults:
             key = raw.lstrip('-').split('=')[0]
             # Non sovrascriviamo i protected
             if key not in protected_defaults: 
                 directives_map[key] = raw


        # 3. Parsing Direttive Utente (dal JSON, Override Finale)
        user_directives = global_opts.get('sbatch_directives', [])
        
        # Supporto legacy: se l'utente passa un dict invece di una lista, lo convertiamo
        if isinstance(user_directives, dict):
            converted = []
            for k, v in user_directives.items():
                if v is True: converted.append(f"--{k}")
                elif v is False: continue
                else: converted.append(f"--{k}={v}")
            user_directives = converted

        for raw_directive in user_directives:
            directive = str(raw_directive).strip()
            
            # A. Security Check (Newline Injection)
            if '\n' in directive or '\r' in directive:
                self.log.warning(f"Skipping directive containing newlines: {directive}")
                continue

            # B. Estrazione Chiave (Key Extraction)
            # Esempio: "--account=ABC" -> "account"
            # Esempio: "--exclusive" -> "exclusive"
            # Esempio: "-J jobname" -> "J"
            clean_str = directive.lstrip('-')
            if '=' in clean_str:
                key = clean_str.split('=')[0]
            else:
                key = clean_str.split()[0] # Gestisce casi rari come "-J name" se passati come stringa unica
            
            # C. Conflict Resolution
            if key in protected_defaults:
                self.log.warning(f"User directive '{directive}' ignored. '{key}' is managed by CRAB.")
                continue
            
            if key in ['output', 'error', 'o', 'e']:
                self.log.warning(f"User overrode log path with '{directive}'. Standard logging might be lost.")
            
            # D. Apply (Last write wins for user defaults, except protected)
            directives_map[key] = directive

        # 4. Rendering
        # Restituiamo i valori (le stringhe complete)
        return [f"#SBATCH {v}" for v in directives_map.values()]

    def _run_orchestrator(self, config: Dict[str, Any], environment: Dict[str, Any]):
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

        g_opts = config.get('global_options', {})
        data_path = g_opts.get('datapath', os.path.join(CRAB_ROOT, 'data'))
        num_nodes = int(g_opts.get('numnodes'))
        
        os.makedirs(data_path, exist_ok=True)

        # 1. Genera timestamp base
        timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
        
        # 2. Cerca il nome custom nelle opzioni
        custom_name = g_opts.get('name', '')
        
        if custom_name:
            # Sanificazione: mantieni solo alfanumerici, trattini e underscore
            # Sostituisci spazi o altri caratteri con '_'
            safe_name = "".join([c if c.isalnum() or c in ('-', '_') else '_' for c in str(custom_name)])
            # Formato: NAME_TIMESTAMP
            folder_name = f"{safe_name}_{timestamp_str}"
        else:
            # Fallback legacy: solo TIMESTAMP
            folder_name = timestamp_str

        # 3. Costruzione path finale
        runner_id = (environment.get("CRAB_SYSTEM", "unknown") + "/" + folder_name)
        data_directory = os.path.join(data_path, runner_id)
        # --------------------------------------

        os.makedirs(data_directory, exist_ok=True)

        with open(os.path.join(data_directory, 'config.json'), 'w') as f:
            json.dump(config, f, indent=4)
        with open(os.path.join(data_directory, 'environment.json'), 'w') as f:
            json.dump(environment, f, indent=4)

        # --- GENERAZIONE HEADER SBATCH DINAMICO ---
        sbatch_headers = self._generate_sbatch_header(g_opts, data_directory)

        script_path = os.path.join(data_directory, 'crab_job.sh')
        cmd = f"{sys.executable} {os.path.abspath(sys.argv[0])} worker --workdir {data_directory}"
        
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\n\n")
            
            # Scrittura direttive calcolate
            for line in sbatch_headers:
                f.write(f"{line}\n")
            

            venv = os.path.join(CRAB_ROOT, '.venv', 'bin', 'activate')
            if os.path.exists(venv):
                f.write(f"\nsource {venv}\n")

            # Recuperiamo la lista passata dall'Orchestrator nel config
            system_header = g_opts.get('system_header', [])
            if system_header:
                f.write("\n# --- System Setup (Modules & Environment) ---\n")
                for line in system_header:
                    f.write(f"{line}\n")
            
            f.write(f"\n{cmd}\n")

        self.log.info(f"Submitting: sbatch {script_path}")
        out = subprocess.check_output(['sbatch', script_path], text=True)
        self.log.info(out.strip())



    def _run_worker(self, config: Dict[str, Any], environment: Dict[str, Any], output_dir: str):
        self.log.info("Worker started")
        
        orig_env = os.environ.copy()
        
        # Safely expand and inject the framework delta into the compute node's native environment
        for key, val in environment.items():
            os.environ[key] = os.path.expandvars(str(val))

        try:
            node_file = "worker_nodelist.txt"
            with open(node_file, "w") as f:
                subprocess.call(["scontrol", "show", "hostnames", os.environ.get('SLURM_NODELIST')], stdout=f)
            nodes_df = pandas.read_csv(node_file, header=None)
            full_node_list = nodes_df.iloc[:, 0].tolist()
            self.log.info(f"Allocated {len(full_node_list)} node(s)")
            
            global_opts = config.get('global_options', {})
            experiments = config.get('experiments', {})
            sorted_exp_ids = sorted(experiments.keys())
            total_exps = len(sorted_exp_ids)

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
                )
                try:
                    runner.setup()
                    runner.execute(output_dir)
                    runner.save_results()
                except Exception as e:
                    self.log.error(f"Experiment {exp_id} failed: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    runner.teardown()
                    time.sleep(2)
            
            self.log.info("All experiments finished")

        finally:
            os.environ.clear()
            os.environ.update(orig_env)
            if os.path.exists("worker_nodelist.txt"):
                os.remove("worker_nodelist.txt")
