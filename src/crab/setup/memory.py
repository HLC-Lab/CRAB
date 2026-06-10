import os
import json
import shutil
import glob
from typing import Dict, Optional, Any
from rich.console import Console
from pathlib import Path

console = Console()

# Calculate the absolute physical root of the CRAB framework
_base_path = Path(__file__).resolve().parents[3]

CRAB_ROOT = str(_base_path)
CONFIG_DIR = os.path.join(CRAB_ROOT, "config")
ENV_DIR = os.path.join(CONFIG_DIR, "environments")

def ensure_env_dir():
    """Ensures the environments directory exists."""
    os.makedirs(ENV_DIR, exist_ok=True)

def save_receipt(benchmark_id: str, receipt: Dict[str, Any]):
    """Saves a rich environment receipt for a benchmark."""
    ensure_env_dir()
    receipt_file = os.path.join(ENV_DIR, f"{benchmark_id}.json")
    
    with open(receipt_file, 'w') as f:
        json.dump(receipt, f, indent=4)

def get_receipt(benchmark_id: str) -> Optional[Dict[str, Any]]:
    """Loads a benchmark receipt. Returns None if not configured."""
    receipt_file = os.path.join(ENV_DIR, f"{benchmark_id}.json")
    
    # Fallback to check if it's not configured
    if not os.path.exists(receipt_file):
        return None
        
    try:
        with open(receipt_file, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        # FAILSAFE: Backup corrupted receipt
        backup_file = f"{receipt_file}.bak"
        shutil.copy2(receipt_file, backup_file)
        
        console.print(f"[bold red]Warning:[/bold red] The receipt [yellow]{receipt_file}[/yellow] is corrupted.")
        console.print(f"A backup has been saved to [yellow]{backup_file}[/yellow].")
        return None
    except Exception as e:
        console.print(f"[bold red]Error reading receipt {benchmark_id}:[/bold red] {e}")
        return None

def remove_receipt(benchmark_id: str):
    """Safely removes a benchmark receipt."""
    receipt_file = os.path.join(ENV_DIR, f"{benchmark_id}.json")
    if os.path.exists(receipt_file):
        os.remove(receipt_file)

def get_all_receipts() -> Dict[str, Dict[str, Any]]:
    """Returns a dictionary of all configured benchmarks and their receipts."""
    ensure_env_dir()
    receipts = {}
    
    for file in glob.glob(os.path.join(ENV_DIR, "*.json")):
        bench_id = Path(file).stem
        receipt = get_receipt(bench_id)
        if receipt:
            receipts[bench_id] = receipt
            
    return receipts
