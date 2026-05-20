import os
import shutil
import subprocess
from collections import deque
from rich.console import Console, Group
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

import questionary
from questionary import Choice

import crab.setup.memory as memory
from crab.setup.registry import discover_recipes

console = Console()

_SETUP_DIR = os.path.dirname(os.path.abspath(__file__))
CRAB_ROOT = os.path.abspath(os.path.join(_SETUP_DIR, "..", "..", ".."))
BENCHMARKS_DIR = os.path.join(CRAB_ROOT, "benchmarks")

def print_header(total_recipes: int | None = None, title: str = "Welcome to the CRAB Setup Wizard"):
    console.clear()
    console.print(Panel.fit(f"[bold cyan]🦀 {title}[/bold cyan]", border_style="cyan"))
    if total_recipes is not None:
        console.print(f"Found [bold]{total_recipes}[/bold] supported benchmarks.\n")

def run_deep_search(binary_name: str) -> str | None:
    console.print(f"[dim]Running deep search for '{binary_name}' in ~/... This might take a minute.[/dim]")
    try:
        home_dir = os.path.expanduser("~")
        result = subprocess.run(
            ["find", home_dir, "-name", binary_name, "-type", "f", "-executable"],
            capture_output=True, text=True
        )
        paths = [p for p in result.stdout.strip().split("\n") if p]
        if paths: return paths[0]
    except Exception as e:
        console.print(f"[red]Deep search failed: {e}[/red]")
    return None

def handle_cleanup(benchmark_id: str):
    receipt = memory.get_receipt(benchmark_id)
    if receipt and receipt.get("type") == "source":
        old_path = receipt.get("binary_path", "")
        if old_path.startswith(BENCHMARKS_DIR):
            rel_path = os.path.relpath(old_path, BENCHMARKS_DIR)
            base_folder = rel_path.split(os.sep)[0]
            full_dir_to_delete = os.path.join(BENCHMARKS_DIR, base_folder)
            
            if os.path.exists(full_dir_to_delete):
                if Confirm.ask(f"[yellow]Found old build at {full_dir_to_delete}. Delete it to save space?[/yellow]", default=True):
                    shutil.rmtree(full_dir_to_delete)
                    console.print(f"[green]Cleaned up {full_dir_to_delete}[/green]")

def run():
    os.makedirs(BENCHMARKS_DIR, exist_ok=True)
    recipes = discover_recipes()
    
    if not recipes:
        print_header()
        console.print("[red]No benchmark recipes found in registry. Exiting.[/red]")
        return

    print_header(len(recipes))
    console.print("[bold]Select the benchmarks you want to install or configure:[/bold]")
    console.print("[dim](Use Space to toggle, Up/Down to navigate, Enter to confirm)[/dim]\n")

    choices = []
    for recipe in recipes:
        receipt = memory.get_receipt(recipe.benchmark_id)
        if receipt:
            title = f"{recipe.name} (Configured: {receipt.get('type')})"
            choices.append(Choice(title=title, value=recipe, checked=False))
        else:
            title = f"{recipe.name} (Not configured)"
            choices.append(Choice(title=title, value=recipe, checked=True))

    selected_recipes = questionary.checkbox(
        "Benchmarks:", choices=choices, qmark="🦀",
        style=questionary.Style([('highlighted', 'fg:cyan bold')])
    ).ask()

    if not selected_recipes:
        console.print("\n[yellow]No benchmarks selected. Exiting.[/yellow]")
        return

    total_selected = len(selected_recipes)

    for i, recipe in enumerate(selected_recipes):
        print_header(title=f"Configuring {recipe.name} ({i + 1}/{total_selected})")
        
        receipt = memory.get_receipt(recipe.benchmark_id)
        if receipt:
            console.print(f"✅ [bold green]{recipe.name}[/bold green] is already configured via {receipt.get('type')}.")
            console.print(f"   Binary: [dim]{receipt.get('binary_path')}[/dim]\n")
            if not Confirm.ask(f"Do you want to completely reconfigure {recipe.name}?", default=False):
                continue
        else:
            console.print(f"❌ [bold yellow]{recipe.name}[/bold yellow] is not configured.\n")

        console.print("[bold]How would you like to configure it?[/bold]")
        console.print("  [1] Auto-detect existing installation")
        console.print("  [2] Provide manual path")
        console.print("  [3] Load via Environment Module (e.g. module load qe/7.4)")
        console.print(f"  [4] Download and build from source (into {BENCHMARKS_DIR})")
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4"], default="1")
        
        final_path = None
        receipt_type = "binary"
        pre_run_hooks = recipe.pre_run_hooks.copy()

        if choice == "1":
            console.print("[dim]Running fast search...[/dim]")
            final_path = recipe.fast_search(BENCHMARKS_DIR)
            if final_path:
                console.print(f"[green]Found executable at:[/green] {final_path}")
            else:
                if Confirm.ask("[yellow]Fast search failed. Run deep system search? (May be slow)[/yellow]", default=False):
                    final_path = run_deep_search(recipe.benchmark_id.lower())
                    if final_path and recipe.verify_existing(final_path):
                        console.print(f"[green]Found executable at:[/green] {final_path}")
                    else:
                        console.print("[red]Deep search failed to find a valid executable.[/red]")
                        final_path = None

        elif choice == "2":
            while True:
                user_path = Prompt.ask("Enter the absolute path to the executable/folder (or 'q' to cancel)")
                if user_path.lower() == 'q': break
                if recipe.verify_existing(user_path):
                    final_path = user_path
                    break
                else:
                    console.print("[red]Invalid path or file is not executable. Try again.[/red]")

        elif choice == "3":
            # TRACK A: Module Loading
            receipt_type = "module"
            module_cmd = Prompt.ask("Enter the exact module command (e.g., 'module load quantum-espresso')")
            binary_name = Prompt.ask("Enter the executable name provided by the module (e.g., 'pw.x')")
            
            pre_run_hooks.insert(0, module_cmd) # Inject module load before recipe hooks
            final_path = binary_name

        elif choice == "4":
            # TRACK B: Source Build
            success, msg = recipe.check_dependencies()
            if not success:
                console.print(f"\n[bold red]Dependency Check Failed:[/bold red] {msg}")
                console.print("[yellow]Skipping build. Please load required modules and try again.[/yellow]")
                console.input("\n[dim]Press [Enter] to continue...[/dim]")
                continue

            handle_cleanup(recipe.benchmark_id)
            target_dir = os.path.join(BENCHMARKS_DIR, recipe.benchmark_id)
            
            current_step = "Initializing..."
            recent_logs = deque(maxlen=6)
            
            def render_build_ui() -> Panel:
                step_text = Text(f"🟢 {current_step}", style="bold yellow")
                log_text = Text.from_markup("\n".join(f"> [dim]{log}[/dim]" for log in recent_logs))
                return Panel(Group(step_text, Text(""), log_text), title=f"[cyan]Building {recipe.name}[/cyan]", border_style="cyan")

            with Live(render_build_ui(), console=console, refresh_per_second=15) as live:
                def live_callback(msg_type: str, msg: str):
                    nonlocal current_step
                    if msg_type == "step": current_step = msg
                    elif msg_type == "log": recent_logs.append(msg[:120] + "..." if len(msg) > 120 else msg)
                    live.update(render_build_ui())
                    
                success, result = recipe.download_and_build(target_dir, log_callback=live_callback)

            if success:
                final_path = result
                receipt_type = "source"
                console.print(f"\n[bold green]Build successful![/bold green] Located at: {final_path}")
            else:
                console.print(f"\n[bold red]Build failed:[/bold red]\n{result}")
                console.input("\n[dim]Press [Enter] to continue...[/dim]")
                continue

        # Create and save the Environment Receipt
        if final_path:
            new_receipt = {
                "id": recipe.benchmark_id,
                "type": receipt_type,
                "binary_path": final_path,
                "launcher_override": recipe.launcher_override,
                "hooks": {
                    "pre_run": pre_run_hooks,
                    "post_run": []
                }
            }
            memory.save_receipt(recipe.benchmark_id, new_receipt)
            console.print(f"\n[bold green]✅ {recipe.name} receipt generated successfully![/bold green]")
        else:
            console.print(f"\n[yellow]⚠️ {recipe.name} configuration skipped or failed.[/yellow]")

    print_header(title="Setup Complete")
    console.print(Panel.fit("[bold green]All requested benchmarks have been processed.[/bold green]\nRun CRAB Orchestrator to start benchmarking.", border_style="green"))

if __name__ == "__main__":
    run()
