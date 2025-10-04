import json
import os

# --- PARAMETRI DI CONFIGURAZIONE ---

# Path di base per i wrapper
BASE_PATH = "/leonardo/home/userexternal/mmarcel3/CRAB/wrappers"

# Nodi da usare per i test
# Puoi cambiarlo facilmente qui per generare test per diversi scale
NUM_NODES = 16

# Benchmarks da usare come "victim"
# Selezionati in base al paper e ai file disponibili
VICTIMS = {
    "ring_nb": f"{BASE_PATH}/ring_nb.py",
    "pw_ping_pong_b": f"{BASE_PATH}/pw-ping-pong_b.py"
}

# Benchmarks da usare come "aggressor"
AGGRESSORS = {
    "a2a_b": f"{BASE_PATH}/a2a_b.py",  # All-to-all: congestione intermedia
    "inc_b": f"{BASE_PATH}/inc_b.py"   # Incast: congestione degli endpoint
}
DUMMY_AGGRESSOR = {
    "null_dummy": f"{BASE_PATH}/null_dummy.py"
}

# Dimensioni dei messaggi da testare (in byte)
# Come da paper: piccoli, medi, grandi
MESSAGE_SIZES = {
    "128B": "128",
    "16KiB": "16384",
    "1MiB": "1048576"
}

# Strategie di allocazione
ALLOCATION_MODES = {
    "linear": "l",
    "random": "r",
    "interleaved": "i"
}

# Rapporto di nodi tra victim e aggressor (victim:aggressor)
ALLOCATION_SPLITS = ["50:50", "90:10", "10:90"]


# --- FUNZIONI DELLO SCRIPT ---

def create_config(victim_path, aggressor_path, msg_size, num_nodes, alloc_mode_key, alloc_split):
    """Crea una singola configurazione JSON con criteri di convergenza intelligenti."""

    victim_name = os.path.basename(victim_path).replace('.py', '')
    aggressor_name = os.path.basename(aggressor_path).replace('.py', '')

    config = {
        "global_options": {
            "nodes": "idle",
            "numnodes": str(num_nodes),
            "allocationmode": ALLOCATION_MODES[alloc_mode_key],
            "allocationsplit": alloc_split,

            # --- STRATEGIA DI ESECUZIONE OTTIMIZZATA ---
            "minruns": "30",          # Periodo di riscaldamento prima di controllare la convergenza
            "maxruns": "500",         # Limite massimo di sicurezza
            "alpha": "0.05",          # Confidenza del 95% (standard)
            "beta": "0.05",           # Precisione del 5% (standard)
            "convergeall": True,      # Attendi che tutte le metriche convergano

            "timeout": "1800.0",      # Timeout generale in secondi (30 min)
            "ppn": "1",
            "outformat": "csv",
            "runtimeout": "stdout",
            "seed": "42",
            "extrainfo": f"{victim_name}_vs_{aggressor_name}_{num_nodes}n_{alloc_mode_key}_{alloc_split.replace(':', '-')}_{MESSAGE_SIZES_INV[msg_size]}"
        },
        "applications": {
            "0": {
                "path": victim_path,
                "args": f"-msgsize {msg_size} -iter 2000",
                "collect": True,
                "start": "5",
                "end": ""
            },
            "1": {
                "path": aggressor_path,
                # NOTA: -iter 99999 va bene, l'aggressore deve girare continuamente
                "args": f"-msgsize {msg_size} -iter 99999",
                "collect": False,
                "start": "0",
                "end": "f"
            }
        }
    }

    if "null_dummy" in aggressor_path:
        config["applications"]["1"]["args"] = ""

    return config

def generate_all_configs():
    """Genera tutti i file di configurazione."""

    # Creazione delle directory
    base_dir = "configs"
    baseline_dir = os.path.join(base_dir, "1_baselines")
    congestion_dir = os.path.join(base_dir, "2_congestion_tests")
    os.makedirs(baseline_dir, exist_ok=True)
    os.makedirs(congestion_dir, exist_ok=True)

    print("--- Generating Baseline Configs ---")
    # 1. Genera le configurazioni di baseline
    for v_name, v_path in VICTIMS.items():
        for size_key, size_val in MESSAGE_SIZES.items():
            # Per la baseline, la modalità di allocazione non è critica, usiamo linear
            config_data = create_config(v_path, DUMMY_AGGRESSOR["null_dummy"], size_val, NUM_NODES, "linear", "50:50")

            filename = f"baseline_{v_name}_{NUM_NODES}n_{size_key}.json"
            filepath = os.path.join(baseline_dir, filename)

            with open(filepath, 'w') as f:
                json.dump(config_data, f, indent=4)
            print(f"Generated: {filepath}")

    print("\n--- Generating Congestion Test Configs ---")
    # 2. Genera le configurazioni di congestione
    for v_name, v_path in VICTIMS.items():
        for a_name, a_path in AGGRESSORS.items():
            test_subdir = os.path.join(congestion_dir, f"{v_name}_vs_{a_name}")
            os.makedirs(test_subdir, exist_ok=True)

            for mode_key in ALLOCATION_MODES.keys():
                for split in ALLOCATION_SPLITS:
                    for size_key, size_val in MESSAGE_SIZES.items():
                        config_data = create_config(v_path, a_path, size_val, NUM_NODES, mode_key, split)

                        filename = f"{v_name}_vs_{a_name}_{NUM_NODES}n_{mode_key}_{split.replace(':', '-')}_{size_key}.json"
                        filepath = os.path.join(test_subdir, filename)

                        with open(filepath, 'w') as f:
                            json.dump(config_data, f, indent=4)
                        print(f"Generated: {filepath}")

if __name__ == "__main__":
    # Inverti il dizionario per la ricerca del nome della dimensione del messaggio
    MESSAGE_SIZES_INV = {v: k for k, v in MESSAGE_SIZES.items()}
    generate_all_configs()
    print("\nGeneration complete!")
