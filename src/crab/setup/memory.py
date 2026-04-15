import os
import json
import shutil
from typing import Dict, Optional
from rich.console import Console

# We'll use Rich for safe warning prints
console = Console()

# Define standard paths
CRAB_ROOT = os.getcwd()
CONFIG_DIR = os.path.join(CRAB_ROOT, "config")
PATHS_FILE = os.path.join(CONFIG_DIR, "paths.json")

def ensure_config_dir():
    """Ensures the config directory exists."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

def load_paths() -> Dict[str, str]:
    """
    Loads the configured benchmark paths from paths.json.
    Includes a failsafe for corrupted JSON files.
    """
    if not os.path.exists(PATHS_FILE):
        return {}
        
    try:
        with open(PATHS_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        # THE FAILSAFE: Backup the corrupted file instead of overwriting it
        backup_file = f"{PATHS_FILE}.bak"
        shutil.copy2(PATHS_FILE, backup_file)
        
        console.print(f"[bold red]Warning:[/bold red] The file [yellow]{PATHS_FILE}[/yellow] is corrupted.")
        console.print(f"A backup has been saved to [yellow]{backup_file}[/yellow].")
        console.print("Starting with a fresh configuration.")
        
        return {}
    except Exception as e:
        console.print(f"[bold red]Error reading paths.json:[/bold red] {e}")
        return {}

def save_path(benchmark_env_key: str, absolute_path: str):
    """Saves or updates a single benchmark path in paths.json."""
    ensure_config_dir()
    paths = load_paths()
    paths[benchmark_env_key] = absolute_path
    
    with open(PATHS_FILE, 'w') as f:
        json.dump(paths, f, indent=4)

def get_path(benchmark_env_key: str) -> Optional[str]:
    """Returns the path for a specific benchmark, or None if not configured."""
    paths = load_paths()
    return paths.get(benchmark_env_key)

def remove_path(benchmark_env_key: str):
    """Removes a benchmark from the paths configuration safely."""
    paths = load_paths()
    if benchmark_env_key in paths:
        del paths[benchmark_env_key]
        with open(PATHS_FILE, 'w') as f:
            json.dump(paths, f, indent=4)
