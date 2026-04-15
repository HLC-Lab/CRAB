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

    console.print(f"Found [bold]{len(recipes)}[/bold] supported benchmarks.\n")

    for recipe in recipes:
        current_path = memory.get_path(recipe.env_key)
        
        # Display current status
        if current_path:
            console.print(f"✅ [bold green]{recipe.name}[/bold green] is already configured.")
            console.print(f"   Path: [dim]{current_path}[/dim]")
            if not Confirm.ask(f"Do you want to reconfigure {recipe.name}?", default=False):
                console.print()
                continue
        else:
            console.print(f"❌ [bold yellow]{recipe.name}[/bold yellow] is not configured.")
            if not Confirm.ask(f"Do you want to configure {recipe.name} now?", default=True):
                console.print()
                continue

        # Choose Action
        console.print("\n[bold]How would you like to configure it?[/bold]")
        console.print("  [1] Auto-detect existing installation")
        console.print("  [2] Provide manual path")
        console.print(f"  [3] Download and build from source (into {BENCHMARKS_DIR})")
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="1")
        
        final_path = None

        if choice == "1":
            # Tier 1 Search
            console.print("[dim]Running fast search...[/dim]")
            final_path = recipe.fast_search(BENCHMARKS_DIR)
            
            if final_path:
                console.print(f"[green]Found executable at:[/green] {final_path}")
            else:
                # Tier 2 Search
                binary_name = recipe.env_key.replace("CRAB_", "").replace("_PATH", "").lower()
                if Confirm.ask("[yellow]Fast search failed. Run deep system search? (May be slow)[/yellow]", default=False):
                    final_path = run_deep_search(binary_name)
                    if final_path and recipe.verify_existing(final_path):
                        console.print(f"[green]Found executable at:[/green] {final_path}")
                    else:
                        console.print("[red]Deep search failed to find a valid executable.[/red]")
                        final_path = None

        elif choice == "2":
            # Manual Path
            while True:
                user_path = Prompt.ask("Enter the absolute path to the executable (or 'q' to cancel)")
                if user_path.lower() == 'q':
                    break
                if recipe.verify_existing(user_path):
                    final_path = user_path
                    break
                else:
                    console.print("[red]Invalid path or file is not executable. Try again.[/red]")

        elif choice == "3":
            # Build from Source
            success, msg = recipe.check_dependencies()
            if not success:
                console.print(f"[bold red]Dependency Check Failed:[/bold red] {msg}")
                console.print("[yellow]Skipping build. Please load required modules and try again.[/yellow]")
                continue
                
            handle_cleanup(recipe.env_key)
            
            target_dir = os.path.join(BENCHMARKS_DIR, recipe.name.lower().replace(" ", "_"))
            console.print(f"[cyan]Downloading and building {recipe.name}...[/cyan]")
            
            with console.status("[bold green]Compiling (this may take a while)...[/bold green]"):
                success, result = recipe.download_and_build(target_dir)
                
            if success:
                final_path = result
                console.print(f"[bold green]Build successful![/bold green] Binary located at: {final_path}")
            else:
                console.print(f"[bold red]Build failed:[/bold red]\n{result}")

        # Save result if successful
        if final_path:
            memory.save_path(recipe.env_key, final_path)
            console.print(f"[bold green]{recipe.name} configured successfully![/bold green]\n")
        else:
            console.print(f"[yellow]{recipe.name} configuration skipped/failed.[/yellow]\n")

    console.print(Panel.fit("[bold green]Setup Complete![/bold green]\nRun CRAB Orchestrator to start benchmarking.", border_style="green"))

if __name__ == "__main__":
    run()
