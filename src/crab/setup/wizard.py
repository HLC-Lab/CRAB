import os
import shutil
import subprocess
import env_modules_python  # Ensure environment wrapper dependencies exist or use subshell tracking
from collections import deque
from typing import Dict, Any

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

def capture_module_environment(module_cmd: str) -> Dict[str, str]:
    """Evaluates module loads in an isolated shell to return precise path mutations."""
    base_env = os.environ.copy()
    if not module_cmd:
        return base_env
    try:
        # Source standard cluster layout variables cleanly
        init_snippet = ". /etc/profile.d/modules.sh" if os.path.exists("/etc/profile.d/modules.sh") else "true"
        full_cmd = f"{init_snippet} && {module_cmd} && env"
        result = subprocess.run(["bash", "-c", full_cmd], capture_output=True, text=True, check=True)
        
        parsed_env = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                parsed_env[k] = v
        return parsed_env
    except Exception as e:
        console.print(f"[yellow]Warning: Environment module pre-evaluation limited: {e}[/yellow]")
        return base_env

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
            target_cleanup = os.path.join(BENCHMARKS_DIR, benchmark_id)
            if os.path.exists(target_cleanup):
                if Confirm.ask(f"[yellow]Found old build path at {target_cleanup}. Clear layout directory?[/yellow]", default=True):
                    shutil.rmtree(target_cleanup)
                    console.print(f"[green]Cleaned up historical records: {target_cleanup}[/green]")

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
            console.print(f"✅ [bold green]{recipe.name}[/bold green] is already configured.")
            console.print(f"   Binary Path: [dim]{receipt.get('binary_path')}[/dim]\n")
            if not Confirm.ask(f"Do you want to completely reconfigure {recipe.name}?", default=False):
                continue
        else:
            console.print(f"❌ [bold yellow]{recipe.name}[/bold yellow] is not configured.\n")

        console.print("[bold]Select Configuration Strategy:[/bold]")
        console.print("  [1] Auto-detect existing installation environment")
        console.print("  [2] Provide explicit manual path layout")
        console.print("  [3] Map via cluster Environment Module system")
        console.print(f"  [4] Download and compile from source layout")
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4"], default="1")
        
        final_path = None
        receipt_type = "binary"
        pre_run_hooks = recipe.pre_run_hooks.copy()
        runtime_meta = {}

        if choice == "1":
            final_path = recipe.fast_search(BENCHMARKS_DIR)
            if not final_path:
                if Confirm.ask("[yellow]Fast search skipped. Trigger deep home directory search?[/yellow]", default=False):
                    final_path = run_deep_search(recipe.benchmark_id.lower())

        elif choice == "2":
            while True:
                user_path = Prompt.ask("Enter the absolute path to executable/directory (or 'q' to exit)")
                if user_path.lower() == 'q': break
                if recipe.verify_existing(user_path):
                    final_path = user_path
                    break
                console.print("[red]Invalid path configuration or target file properties. Retry.[/red]")

        elif choice == "3":
            receipt_type = "module"
            module_cmd = Prompt.ask("Enter exact module command (e.g., 'module load quantum-espresso/7.4.1')")
            binary_name = Prompt.ask("Enter target executable binary name (e.g., 'pw.x')", default=recipe.benchmark_id)
            pre_run_hooks.insert(0, module_cmd)
            final_path = binary_name

        elif choice == "4":
            receipt_type = "source"
            manifest = recipe.build_manifest
            target_env = os.environ.copy()
            
            if manifest.requires_modules:
                mod_cmd = Prompt.ask("Enter necessary pre-build cluster module loads", default="module load gcc openmpi")
                target_env = capture_module_environment(mod_cmd)
                if mod_cmd:
                    pre_run_hooks.append(mod_cmd)

            # Evaluate Declarative Parameters Dynamic Discovery
            user_params = {}
            for param in manifest.parameters:
                if param.choices:
                    val = Prompt.ask(param.description, choices=param.choices, default=param.default)
                else:
                    val = Prompt.ask(param.description, default=param.default)
                user_params[param.name] = val

            success, dep_msg = recipe.check_dependencies(target_env)
            if not success:
                console.print(f"\n[bold red]Pre-Flight Dependency Fail:[/bold red] {dep_msg}")
                console.input("\n[dim]Press [Enter] to continue...[/dim]")
                continue

            handle_cleanup(recipe.benchmark_id)
            target_dir = os.path.join(BENCHMARKS_DIR, recipe.benchmark_id)
            
            current_step = "Initializing workspace environments..."
            recent_logs = deque(maxlen=6)
            
            def render_build_ui() -> Panel:
                step_text = Text(f"🟢 {current_step}", style="bold yellow")
                log_text = Text.from_markup("\n".join(f"> [dim]{log}[/dim]" for log in recent_logs))
                return Panel(Group(step_text, Text(""), log_text), title=f"[cyan]Compiling {recipe.name}[/cyan]", border_style="cyan")

            with Live(render_build_ui(), console=console, refresh_per_second=15) as live:
                def live_callback(msg_type: str, msg: str):
                    nonlocal current_step
                    if msg_type == "step": current_step = msg
                    elif msg_type == "log": recent_logs.append(msg[:120] + "..." if len(msg) > 120 else msg)
                    live.update(render_build_ui())
                    
                success, build_result, err_msg = recipe.download_and_build(target_dir, user_params, target_env, log_callback=live_callback)

            if success and build_result:
                final_path = build_result.binary_path
                runtime_meta = build_result.metadata
            else:
                console.print(f"\n[bold red]Compilation Interrupted:[/bold red]\n{err_msg}")
                console.input("\n[dim]Press [Enter] to continue...[/dim]")
                continue

        if final_path:
            new_receipt = {
                "id": recipe.benchmark_id,
                "type": receipt_type,
                "binary_path": final_path,
                "launcher_override": recipe.launcher_override,
                "hooks": {"pre_run": pre_run_hooks, "post_run": []}
            }
            new_receipt.update(runtime_meta)
            memory.save_receipt(recipe.benchmark_id, new_receipt)
            console.print(f"\n[bold green]=== {recipe.name} receipt generated successfully ===[/bold green]\n")
        else:
            console.print(f"\n[yellow]⚠️ {recipe.name} action skipped or path mapping incomplete.[/yellow]\n")

    print_header(title="Configuration Phase Terminated")
    console.print(Panel.fit("[bold green]All targeted receipt setups have been synchronized successfully.[/bold green]", border_style="green"))

if __name__ == "__main__":
    run()
