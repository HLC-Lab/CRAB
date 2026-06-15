import os
import re
import shutil
import subprocess
from collections import deque
from typing import Dict, Any, List, Optional

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


# ── Shared utilities ──────────────────────────────────────────────────────────


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


def run_deep_search(binary_name: str) -> Optional[str]:
    console.print(f"[dim]Running deep search for '{binary_name}' in ~/... This might take a minute.[/dim]")
    try:
        home_dir = os.path.expanduser("~")
        result = subprocess.run(
            ["find", home_dir, "-name", binary_name, "-type", "f", "-executable"],
            capture_output=True, text=True,
        )
        paths = [p for p in result.stdout.strip().split("\n") if p]
        if paths:
            return paths[0]
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
                    console.print(f"[green]Cleaned up: {target_cleanup}[/green]")


def _group_recipes_by_suite(recipes) -> Dict[str, List]:
    groups: Dict[str, List] = {}
    for recipe in recipes:
        suite = getattr(recipe, "suite", recipe.name)
        groups.setdefault(suite, []).append(recipe)
    return groups


def _shorten_path(path: str, max_len: int = 48) -> str:
    """Truncates a path from the left so it fits within max_len characters."""
    if len(path) <= max_len:
        return path
    if max_len <= 1:
        return "…"
    return "…" + path[-(max_len - 1):]


# ── Custom benchmark wizard ───────────────────────────────────────────────────


def _derive_benchmark_id(name: str) -> str:
    """Converts a display name into a safe benchmark ID (lowercase, underscored)."""
    sanitized = name.lower().strip()
    sanitized = re.sub(r"[\s\-]+", "_", sanitized)
    sanitized = re.sub(r"[^a-z0-9_]", "", sanitized)
    return sanitized.strip("_")


def _collect_pre_run_hooks() -> List[str]:
    """Interactively collects pre-run shell commands until the user submits a blank line."""
    hooks = []
    console.print("\n[bold]Pre-run commands[/bold]")
    console.print(
        "[dim]These shell commands run in the job environment before the benchmark starts.[/dim]"
    )
    console.print(
        "[dim]Examples:[/dim]  [dim]module load gcc/12 openmpi/4.1[/dim]\n"
        "          [dim]export OMP_NUM_THREADS=4[/dim]\n"
        "          [dim]ulimit -s unlimited[/dim]"
    )
    console.print("[dim]\nLeave blank and press Enter when done.[/dim]\n")
    while True:
        cmd = Prompt.ask("  Command", default="").strip()
        if not cmd:
            break
        hooks.append(cmd)
    return hooks


def _run_custom_benchmark_wizard(existing_receipt_ids: List[str], known_recipe_ids: List[str]) -> None:
    """
    Guides the user through registering an already-installed benchmark that has no recipe.
    Produces a 'binary' receipt in config/environments/.
    """
    taken_receipt_ids = set(existing_receipt_ids)

    print_header(title="Register Custom Benchmark")

    # ── Step 1: display name → derived ID ────────────────────────────────────
    while True:
        name = Prompt.ask("\n[bold]Benchmark display name[/bold] (e.g. 'HPL Linpack')").strip()
        if not name:
            console.print("[red]Name cannot be empty.[/red]")
            continue

        bench_id = _derive_benchmark_id(name)
        if not bench_id:
            console.print(
                "[red]That name produces an empty ID after sanitisation "
                "(only letters, digits, and underscores are kept). Try a different name.[/red]"
            )
            continue

        console.print(f"\n  Benchmark ID: [bold cyan]{bench_id}[/bold cyan]")

        if bench_id in taken_receipt_ids:
            console.print(
                f"[yellow]  ⚠  A receipt for '[bold]{bench_id}[/bold]' already exists. "
                f"You will be asked before it is overwritten.[/yellow]"
            )

        break

    # ── Step 2: executable path ───────────────────────────────────────────────
    while True:
        path = Prompt.ask(
            "\n[bold]Full path to the executable[/bold] (e.g. '/opt/hpl/bin/xhpl')"
        ).strip()
        if not path:
            console.print("[red]Path cannot be empty.[/red]")
            continue
        if not os.path.isfile(path):
            console.print(f"[yellow]  ⚠  '{path}' does not point to an existing file.[/yellow]")
            if not Confirm.ask("  Register the path anyway?", default=False):
                continue
        break

    # ── Step 3: MPI launcher override ────────────────────────────────────────
    console.print("\n[bold]MPI Launcher[/bold]")
    launcher_choice = questionary.select(
        "How should this benchmark be launched?",
        choices=[
            Choice("mpirun  (OpenMPI / MPICH default)",     value="mpirun"),
            Choice("srun    (SLURM native launcher)",        value="srun"),
            Choice("none    (run directly, no MPI prefix)",  value=""),
        ],
        style=questionary.Style([("highlighted", "fg:cyan bold")]),
    ).ask()

    if launcher_choice is None:
        console.print("[yellow]Registration cancelled.[/yellow]")
        return

    # ── Step 4: pre-run commands ──────────────────────────────────────────────
    pre_run_hooks = _collect_pre_run_hooks()

    # ── Step 5: overwrite guard ───────────────────────────────────────────────
    existing_receipt = memory.get_receipt(bench_id)
    if existing_receipt:
        console.print(f"\n[yellow]⚠  A receipt for '{bench_id}' already exists:[/yellow]")
        console.print(f"   Binary: [dim]{existing_receipt.get('binary_path')}[/dim]")
        console.print(f"   Type:   [dim]{existing_receipt.get('type')}[/dim]")
        if not Confirm.ask("  Overwrite it?", default=False):
            console.print("[yellow]Registration cancelled.[/yellow]")
            console.input("\n[dim]Press [Enter] to continue...[/dim]")
            return

    # ── Step 6: save receipt ──────────────────────────────────────────────────
    receipt = {
        "id": bench_id,
        "type": "binary",
        "binary_path": path,
        "launcher_override": launcher_choice,
        "hooks": {
            "pre_run": pre_run_hooks,
            "post_run": [],
        },
    }
    memory.save_receipt(bench_id, receipt)

    console.print(
        f"\n[bold green]=== '{name}' (ID: {bench_id}) receipt generated successfully ===[/bold green]\n"
    )
    console.input("[dim]Press [Enter] to continue...[/dim]")


# ── Supported-recipes wizard ──────────────────────────────────────────────────


def _run_recipe_wizard(recipes: List, groups: Dict[str, List], recipe_ids: List[str]) -> None:
    """Handles the install/configure flow for all known benchmark recipes."""
    print_header(len(recipes))
    console.print("[bold]Select the benchmarks you want to install or configure:[/bold]")
    console.print("[dim](Space to toggle, Up/Down to navigate, Enter to confirm)[/dim]\n")

    # Build checkbox list with informative status labels
    suite_choices = []
    for suite_name, suite_recipes in groups.items():
        configured_receipts = [memory.get_receipt(r.benchmark_id) for r in suite_recipes]
        configured_receipts = [r for r in configured_receipts if r]
        configured = bool(configured_receipts)

        if configured:
            sample_path = _shorten_path(configured_receipts[0].get("binary_path", "unknown"))
            title = f"  {suite_name:<28}  ✓  {sample_path}"
            checked = False
        else:
            title = f"  {suite_name:<28}  ○  not configured"
            checked = True

        suite_choices.append(Choice(title=title, value=suite_name, checked=checked))

    selected_suite_names = questionary.checkbox(
        "Benchmarks:",
        choices=suite_choices,
        qmark="🦀",
        style=questionary.Style([
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
        ]),
    ).ask()

    if not selected_suite_names:
        console.print("\n[yellow]No benchmarks selected.[/yellow]")
        return

    # Version selection for multi-version suites
    selected_recipes = []
    for suite_name in selected_suite_names:
        suite_recipes = groups[suite_name]
        if len(suite_recipes) == 1:
            selected_recipes.extend(suite_recipes)
        else:
            console.print(f"\n[bold]{suite_name}[/bold] has multiple versions. Select which to install:\n")
            version_choices = []
            for recipe in suite_recipes:
                receipt = memory.get_receipt(recipe.benchmark_id)
                if receipt:
                    path_hint = _shorten_path(receipt.get("binary_path", "unknown"), max_len=36)
                    ver_title = f"  {recipe.name:<32}  ✓  {path_hint}"
                    ver_checked = False
                else:
                    ver_title = f"  {recipe.name:<32}  ○  not configured"
                    ver_checked = True
                version_choices.append(Choice(title=ver_title, value=recipe, checked=ver_checked))

            chosen = questionary.checkbox(
                f"{suite_name} versions:",
                choices=version_choices,
                qmark="🦀",
                style=questionary.Style([
                    ("highlighted", "fg:cyan bold"),
                    ("selected", "fg:green"),
                ]),
            ).ask()
            if chosen:
                selected_recipes.extend(chosen)

    if not selected_recipes:
        console.print("\n[yellow]No versions selected.[/yellow]")
        return

    total_selected = len(selected_recipes)

    # Per-recipe configuration loop
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
        console.print("  [4] Download and compile from source layout")

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
                if user_path.lower() == "q":
                    break
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
                mod_cmd = Prompt.ask(
                    "Enter necessary pre-build cluster module loads",
                    default="module load gcc openmpi",
                )
                target_env = capture_module_environment(mod_cmd)
                if mod_cmd:
                    pre_run_hooks.append(mod_cmd)

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
                log_text = Text.from_markup(
                    "\n".join(f"> [dim]{log}[/dim]" for log in recent_logs)
                )
                return Panel(
                    Group(step_text, Text(""), log_text),
                    title=f"[cyan]Compiling {recipe.name}[/cyan]",
                    border_style="cyan",
                )

            with Live(render_build_ui(), console=console, refresh_per_second=15) as live:
                def live_callback(msg_type: str, msg: str):
                    nonlocal current_step
                    if msg_type == "step":
                        current_step = msg
                    elif msg_type == "log":
                        recent_logs.append(msg[:120] + "..." if len(msg) > 120 else msg)
                    live.update(render_build_ui())

                success, build_result, err_msg = recipe.download_and_build(
                    target_dir, user_params, target_env, log_callback=live_callback
                )

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
                "hooks": {"pre_run": pre_run_hooks, "post_run": []},
            }
            new_receipt.update(runtime_meta)
            memory.save_receipt(recipe.benchmark_id, new_receipt)
            console.print(f"\n[bold green]=== {recipe.name} receipt generated successfully ===[/bold green]\n")
        else:
            console.print(f"\n[yellow]⚠️  {recipe.name} action skipped or path mapping incomplete.[/yellow]\n")

        console.input("\n[dim]Press [Enter] to continue...[/dim]")


# ── Entry point ───────────────────────────────────────────────────────────────


def run():
    os.makedirs(BENCHMARKS_DIR, exist_ok=True)
    recipes = discover_recipes()

    if not recipes:
        print_header()
        console.print("[red]No benchmark recipes found in registry. Exiting.[/red]")
        return

    groups = _group_recipes_by_suite(recipes)
    recipe_ids = [r.benchmark_id for r in recipes]

    print_header()
    console.print("[bold]What would you like to do?[/bold]\n")

    action = questionary.select(
        "Choose an action:",
        choices=[
            Choice("Install or configure a supported benchmark",      value="recipes"),
            Choice("Register a custom already-installed benchmark",   value="custom"),
            Choice("Exit",                                             value="exit"),
        ],
        style=questionary.Style([("highlighted", "fg:cyan bold")]),
        qmark="🦀",
    ).ask()

    if action is None or action == "exit":
        console.print("\n[yellow]Exiting setup.[/yellow]")
        return

    if action == "recipes":
        _run_recipe_wizard(recipes, groups, recipe_ids)
    elif action == "custom":
        current_receipt_ids = list(memory.get_all_receipts().keys())
        _run_custom_benchmark_wizard(current_receipt_ids, recipe_ids)

    print_header(title="Setup Complete")
    console.print(Panel.fit(
        "[bold green]All receipt setups have been synchronised successfully.[/bold green]",
        border_style="green",
    ))


if __name__ == "__main__":
    run()
