import os
import subprocess
import sys

# --- CONFIGURAZIONE ---
# In questa sezione, adatta i parametri al tuo ambiente.

# 1. Il percorso dello script principale del benchmark.
#    Dato che lo lanci da 'CRAB.OLD', 'cli.py' è corretto se anche 
#    questo script si trova in quella cartella.
CRAB_CLI_SCRIPT = 'cli.py'

# 2. Il flag utilizzato per passare il file di configurazione.
CONFIG_FLAG = '-c'

# 3. Eventuali argomenti aggiuntivi che devono essere sempre presenti.
#    Ogni elemento della lista è una parte separata del comando.
EXTRA_ARGS = ['-p', 'leonardo']

# 4. La directory che contiene tutte le configurazioni generate.
CONFIGS_DIR = 'configs'

# --- SCRIPT DI ESECUZIONE (non modificare da qui in poi) ---

def find_json_files(directory):
    """Trova e ordina tutti i file .json in una directory e nelle sue sottodirectory."""
    json_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.json'):
                json_files.append(os.path.join(root, file))
    
    # Ordina i file per assicurarsi che '1_baselines' venga prima di '2_congestion_tests'
    json_files.sort()
    return json_files

def run_benchmark(config_file):
    """Esegue un singolo test di benchmark usando il file di configurazione dato."""
    print(f"--- [STARTING] Test con configurazione: {config_file} ---", flush=True)
    
    # Costruisce il comando completo:
    # Esempio: ['python', 'cli.py', '-c', 'configs/baseline.json', '-p', 'leonardo']
    command_to_run = [
        sys.executable,  # Usa lo stesso interprete python che sta eseguendo questo script
        CRAB_CLI_SCRIPT,
        CONFIG_FLAG,
        config_file
    ] + EXTRA_ARGS
    
    print(f"Esecuzione comando: {' '.join(command_to_run)}", flush=True)
    
    try:
        # Esegue il comando. L'output (stdout, stderr) verrà stampato in tempo reale sulla console.
        subprocess.run(
            command_to_run,
            check=True,         # Lancia un'eccezione se il comando restituisce un errore (exit code != 0)
            text=True,          # Decodifica stdout/stderr come testo
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        print(f"--- [SUCCESS] Test completato: {config_file} ---\n", flush=True)
        return True
        
    except FileNotFoundError:
        print(f"--- [ERROR] Script non trovato: '{CRAB_CLI_SCRIPT}'.", flush=True)
        print("          Assicurati che il percorso in CRAB_CLI_SCRIPT sia corretto e che lo script si trovi nella stessa cartella.", flush=True)
        return False
        
    except subprocess.CalledProcessError as e:
        print(f"--- [FAILED] Il test ha restituito un errore (exit code {e.returncode}): {config_file} ---", flush=True)
        print(f"--- Comando eseguito: {' '.join(e.cmd)} ---\n", flush=True)
        return False
        
    except Exception as e:
        print(f"--- [CRITICAL ERROR] Si è verificato un errore imprevisto durante l'esecuzione di {config_file}: {e} ---\n", flush=True)
        return False

if __name__ == "__main__":
    if not os.path.exists(CONFIGS_DIR):
        print(f"Errore: La directory delle configurazioni '{CONFIGS_DIR}' non è stata trovata.")
        print("Assicurati di eseguire prima lo script 'generate_configs.py' e di trovarti nella stessa directory.")
        sys.exit(1)

    if not os.path.exists(CRAB_CLI_SCRIPT):
        print(f"ERRORE: Lo script del benchmark '{CRAB_CLI_SCRIPT}' non è stato trovato.")
        print("         Assicurati che il nome del file sia corretto e che questo script di esecuzione si trovi nella stessa cartella (CRAB.OLD).")
        sys.exit(1)
        
    config_files = find_json_files(CONFIGS_DIR)
    
    if not config_files:
        print(f"Nessun file di configurazione .json trovato in '{CONFIGS_DIR}'.")
        sys.exit(0)

    print(f"Trovati {len(config_files)} file di configurazione per i test.")
    print("-" * 50)

    success_count = 0
    failed_tests = []

    for i, file_path in enumerate(config_files, 1):
        print(f"Esecuzione test {i} di {len(config_files)}")
        if run_benchmark(file_path):
            success_count += 1
        else:
            failed_tests.append(file_path)
            print("ATTENZIONE: Test fallito. Continuo con il prossimo test...")
            # Se vuoi che lo script si fermi al primo errore, decommenta la riga seguente
            # break 

    print("\n" + "=" * 50)
    print("RIEPILOGO ESECUZIONE")
    print(f"Test completati con successo: {success_count} / {len(config_files)}")
    if failed_tests:
        print("I seguenti test sono falliti:")
        for test in failed_tests:
            print(f"  - {test}")
    print("=" * 50)
