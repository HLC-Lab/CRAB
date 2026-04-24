import os
import shutil
import subprocess
from collections import deque
from rich.console import Console, Group
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

# Import our custom modules
import crab.setup.memory as memory
from crab.setup.registry import discover_recipes

console = Console()
CRAB_ROOT = os.getcwd()
BENCHMARKS_DIR = os.path.join(CRAB_ROOT, "benchmarks")

def print_header(total_recipes: int | None = None):
    """Clears the terminal and prints the pinned Welcome Banner."""
    console.clear()
    console.print(Panel.fit("[bold cyan]🦀 Welcome to the CRAB Setup Wizard[/bold cyan]", border_style="cyan"))
    if total_recipes is not None:
        console.print(f"Found [bold]{total_recipes}[/bold] supported benchmarks.\n")

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
            return paths[0]
    except Exception as e:
        console.print(f"[red]Deep search failed: {e}[/red]")
    return None

def handle_cleanup(env_key: str):
    """Checks if an old CRAB-managed build exists and offers to delete it."""
    old_path = memory.get_path(env_key)
    if old_path and old_path.startswith(BENCHMARKS_DIR):
        rel_path = os.path.relpath(old_path, BENCHMARKS_DIR)
        base_folder = rel_path.split(os.sep)[0]
        full_dir_to_delete = os.path.join(BENCHMARKS_DIR, base_folder)
        
        if os.path.exists(full_dir_to_delete):
            if Confirm.ask(f"[yellow]Found old build at {full_dir_to_delete}. Delete it to save space?[/yellow]", default=True):
                shutil.rmtree(full_dir_to_delete)
                console.print(f"[green]Cleaned up {full_dir_to_delete}[/green]")

def run():
    """Main entry point for the Wizard."""
    os.makedirs(BENCHMARKS_DIR, exist_ok=True)
    recipes = discover_recipes()
    
    if not recipes:
        print_header()
        console.print("[red]No benchmark recipes found in registry. Exiting.[/red]")
        return

    total_recipes = len(recipes)

    for i, recipe in enumerate(recipes):
        print_header(total_recipes)
        console.print(f"[bold]>>> Benchmark {i + 1} of {total_recipes}: {recipe.name}[/bold]\n")

        current_path = memory.get_path(recipe.env_key)
        skip_configuration = False
        
        if current_path:
            console.print(f"✅ [bold green]{recipe.name}[/bold green] is already configured.")
            console.print(f"   Path: [dim]{current_path}[/dim]\n")
            if not Confirm.ask(f"Do you want to reconfigure {recipe.name}?", default=False):
                skip_configuration = True
        else:
            console.print(f"❌ [bold yellow]{recipe.name}[/bold yellow] is not configured.\n")
            if not Confirm.ask(f"Do you want to configure {recipe.name} now?", default=True):
                skip_configuration = True

        if not skip_configuration:
            console.print("\n[bold]How would you like to configure it?[/bold]")
            console.print("  [1] Auto-detect existing installation")
            console.print("  [2] Provide manual path")
            console.print(f"  [3] Download and build from source (into {BENCHMARKS_DIR})")
            
            choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="1")
            final_path = None

            if choice == "1":
                console.print("[dim]Running fast search...[/dim]")
                final_path = recipe.fast_search(BENCHMARKS_DIR)
                
                if final_path:
                    console.print(f"[green]Found executable at:[/green] {final_path}")
                else:
                    binary_name = recipe.env_key.replace("CRAB_", "").replace("_PATH", "").lower()
                    if Confirm.ask("[yellow]Fast search failed. Run deep system search? (May be slow)[/yellow]", default=False):
                        final_path = run_deep_search(binary_name)
                        if final_path and recipe.verify_existing(final_path):
                            console.print(f"[green]Found executable at:[/green] {final_path}")
                        else:
                            console.print("[red]Deep search failed to find a valid executable.[/red]")
                            final_path = None

            elif choice == "2":
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
                success, msg = recipe.check_dependencies()
                if not success:
                    console.print(f"\n[bold red]Dependency Check Failed:[/bold red] {msg}")
                    console.print("[yellow]Skipping build. Please load required modules and try again.[/yellow]")
                else:
                    handle_cleanup(recipe.env_key)
                    target_dir = os.path.join(BENCHMARKS_DIR, recipe.name.lower().replace(" ", "_"))
                    
                    # --- REAL-TIME LIVE BUILD UI ---
                    current_step = "Initializing..."
                    recent_logs = deque(maxlen=6) # Keep the last 6 lines of terminal output
                    
                    def render_build_ui() -> Panel:
                        step_text = Text(f"🟢 {current_step}", style="bold yellow")
                        log_text = Text.from_markup("\n".join(f"> [dim]{log}[/dim]" for log in recent_logs))
                        return Panel(Group(step_text, Text(""), log_text), title=f"[cyan]Building {recipe.name}[/cyan]", border_style="cyan")

                    with Live(render_build_ui(), console=console, refresh_per_second=15) as live:
                        def live_callback(msg_type: str, msg: str):
                            nonlocal current_step
                            if msg_type == "step":
                                current_step = msg
                            elif msg_type == "log":
                                # Truncate extremely long lines so they don't break the panel
                                recent_logs.append(msg[:120] + "..." if len(msg) > 120 else msg)
                            live.update(render_build_ui())
                            
                        success, result = recipe.download_and_build(target_dir, log_callback=live_callback)
                    # -------------------------------

                    if success:
                        final_path = result
                        console.print(f"\n[bold green]Build successful![/bold green] Binary located at: {final_path}")
                    else:
                        console.print(f"\n[bold red]Build failed:[/bold red]\n{result}")

            if final_path:
                memory.save_path(recipe.env_key, final_path)
                console.print(f"\n[bold green]✅ {recipe.name} configured successfully![/bold green]")
            else:
                console.print(f"\n[yellow]⚠️ {recipe.name} configuration skipped or failed.[/yellow]")

        console.input("\n[dim]Press \[Enter] to continue...[/dim]")

    print_header(total_recipes)
    console.print(Panel.fit("[bold green]Setup Complete![/bold green]\nRun CRAB Orchestrator to start benchmarking.", border_style="green"))

if __name__ == "__main__":
    run()
