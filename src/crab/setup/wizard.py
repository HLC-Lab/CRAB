import os
import shutil
import subprocess
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

# Import our custom modules
import crab.setup.memory as memory
from crab.setup.registry import discover_recipes

console = Console()
CRAB_ROOT = os.getcwd()
BENCHMARKS_DIR = os.path.join(CRAB_ROOT, "benchmarks")

def run_deep_search(binary_name: str) -> str | None:
    """Tier 2 Auto-Detect: Searches the user's home directory."""
    console.print(f"[dim]Running deep search for '{binary_name}' in ~/... This might take a minute.[/dim]")
    try:
        home_dir = os.path.expanduser("~")
        result = subprocess.run(
            ["find", home_dir, "-name", binary_name, "-type", "f", "-executable"],
            capture_output=True, text=True
        )
        paths = result.stdout.strip().split("\n")
        paths = [p for p in paths if p] # Filter empty strings
        
        if paths:
            # If multiple are found, just return the first one for simplicity, 
            # or you could expand this to let the user pick from a list.
            return paths[0]
    except Exception as e:
        console.print(f"[red]Deep search failed: {e}[/red]")
    return None

def handle_cleanup(env_key: str):
    """Checks if an old CRAB-managed build exists and offers to delete it."""
    old_path = memory.get_path(env_key)
    if old_path and old_path.startswith(BENCHMARKS_DIR):
        # Extract the base folder of the benchmark (e.g., benchmarks/blink)
        rel_path = os.path.relpath(old_path, BENCHMARKS_DIR)
        base_folder = rel_path.split(os.sep)[0]
        full_dir_to_delete = os.path.join(BENCHMARKS_DIR, base_folder)
        
        if os.path.exists(full_dir_to_delete):
            if Confirm.ask(f"[yellow]Found old build at {full_dir_to_delete}. Delete it to save space?[/yellow]", default=True):
                shutil.rmtree(full_dir_to_delete)
                console.print(f"[green]Cleaned up {full_dir_to_delete}[/green]")

def run():
    """Main entry point for the Wizard."""
    console.print(Panel.fit("[bold cyan]🦀 Welcome to the CRAB Setup Wizard[/bold cyan]", border_style="cyan"))
    
    os.makedirs(BENCHMARKS_DIR, exist_ok=True)
    recipes = discover_recipes()
    
    if not recipes:
        console.print("[red]No benchmark recipes found in registry. Exiting.[/red]")
        return

    console.print(f"Found [bold]{len(recipes)}
