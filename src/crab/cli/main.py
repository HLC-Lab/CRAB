import argparse
import json
import os
import subprocess
import sys

import argcomplete
from argcomplete.completers import FilesCompleter


def _preset_completer(prefix, parsed_args, **kwargs):
    """Dynamically parses presets.json for tab-autocompletion."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    presets_filename = os.path.join(base_dir, "config", "presets.json")
    try:
        with open(presets_filename) as f:
            all_presets = json.load(f)
        valid_presets = [k for k in all_presets.keys() if k not in ["_common", "example_preset"]]
        return [p for p in valid_presets if p.startswith(prefix)]
    except Exception:
        return []


def handle_run(args):
    from crab.cli.orchestrator import execute_orchestrator

    execute_orchestrator(
        args.app_config_file, args.preset, args.log_level, as_json=getattr(args, "json", False)
    )


# --- Machine-readable introspection seam (consumed by the web dashboard) ---


def handle_info(args):
    from crab.cli import contract

    data = contract.gather_info()

    def human(d):
        print(f"CRAB {d['crab_version']}  (contract schema v{d['schema']})")
        print(f"root: {d['crab_root']}")
        print("presets:")
        for p in d["presets"]:
            print(f"  {p['name']:<14} {p['description']}")

    contract.emit(data, args.json, human)


def handle_list_benchmarks(args):
    from crab.cli import contract

    data = contract.gather_benchmarks()

    def human(d):
        print(f"Installed benchmarks ({len(d['benchmarks'])}):")
        for b in d["benchmarks"]:
            arch = f" [{b['target_arch']}]" if b.get("target_arch") else ""
            print(f"  {b['id']}{arch}  ({b.get('type')})")
        print(f"\nWrappers ({len(d['wrappers'])}):")
        for w in d["wrappers"]:
            tag = "" if w["loadable"] else "  (unloadable)"
            print(f"  {w['relpath']}{tag}")

    contract.emit(data, args.json, human)


def handle_nodes(args):
    from crab.cli import contract

    data = contract.gather_nodes()

    def human(d):
        if not d["available"]:
            print(f"sinfo unavailable: {d.get('note', 'n/a')}")
            return
        print("partitions:")
        for p in d["partitions"]:
            print(f"  {p['name']:<18} avail={p.get('avail', '?')} nodes={p.get('nodes', '?')}")
        print(f"node tokens: {len(d['nodes'])}")

    contract.emit(data, args.json, human)


def handle_status(args):
    from crab.cli import contract

    data = contract.gather_status(args.job_ids)

    def human(d):
        for j in d["jobs"]:
            print(f"  {j['job_id']:<12} {j['state']:<12} ({j.get('source', '')})")

    contract.emit(data, args.json, human)


def handle_history(args):
    from crab.cli import contract

    data = contract.gather_history(system=args.system)

    def human(d):
        print(f"{len(d['experiments'])} experiment(s):")
        for e in d["experiments"]:
            print(
                f"  [{e['status']:<9}] {e['system']}/{e['job_name']}/{e['experiment_name']}  "
                f"{e['timestamp']}  apps={e['apps_list']}"
            )

    contract.emit(data, args.json, human)


def handle_setup(args):
    from crab.setup.wizard import run as run_wizard

    run_wizard()


def handle_tui(args):
    """
    Handles the TUI launch with lazy loading and auto-installation
    of optional dependencies.
    """
    try:
        # Architect Protocol: Delayed import to avoid 'Import Tax' on CLI
        from crab.tui.app import BenchmarkApp
    except ImportError:
        # Analyst Protocol: Diagnose missing optional dependencies
        print("[!] TUI dependencies (textual) are not installed.")

        # Interactive prompt using standard input
        try:
            confirm = input(
                "Would you like to install the 'tui' optional dependencies now? (y/N): "
            ).lower()
        except EOFError:
            return

        if confirm == "y":
            print(f"[*] Installing 'textual' and 'textual-fspicker' into {sys.prefix}...")
            try:
                # We use sys.executable to ensure we install into the active .venv
                # We call 'pip install' on the local package with the [tui] extra
                subprocess.check_call([sys.executable, "-m", "pip", "install", "crab[tui]"])
                print("[+] Installation successful. Launching TUI...")

                # Re-attempt the import now that the environment is populated
                from crab.tui.app import BenchmarkApp
            except Exception as e:
                print(f"[ERROR] Auto-installation failed: {e}")
                print("Please run 'pip install -e .[tui]' manually to fix this.")
                return
        else:
            print("Aborting. The TUI requires optional dependencies to run.")
            return

    # Initialize and execute the Textual App
    app = BenchmarkApp()
    app.run()


def handle_web(args):
    """
    Launch the web dashboard with lazy loading and auto-installation
    of optional dependencies (mirrors the TUI launcher).
    """
    try:
        # Delayed import to avoid the 'import tax' on every CLI invocation.
        from crab.web.run import run_server
    except ImportError:
        print("[!] Web dashboard dependencies (fastapi, uvicorn, asyncssh) are not installed.")
        try:
            confirm = input(
                "Would you like to install the 'web' optional dependencies now? (y/N): "
            ).lower()
        except EOFError:
            return

        if confirm == "y":
            print(f"[*] Installing web dependencies into {sys.prefix}...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "crab[web]"])
                print("[+] Installation successful. Launching dashboard...")
                from crab.web.run import run_server
            except Exception as e:
                print(f"[ERROR] Auto-installation failed: {e}")
                print("Please run 'pip install -e .[web]' manually to fix this.")
                return
        else:
            print("Aborting. The web dashboard requires optional dependencies to run.")
            return

    run_server(
        host=args.host, port=args.port, open_browser=not args.no_browser, verbose=args.verbose
    )


def handle_worker(args):
    from crab.cli.orchestrator import execute_worker

    execute_worker(args.workdir, args.log_level)


def handle_export(args):
    from crab.cli.export import handle_export as _handle_export

    _handle_export(args)


def cli_router():
    parser = argparse.ArgumentParser(prog="crab", description="CRAB Benchmarking Framework")
    # 'metavar' is used to hide the "worker" entry
    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="{setup,run,tui,web,export,info,list-benchmarks,nodes,status,history}",
    )
    subparsers.required = True

    # 1. Setup Command
    parser_setup = subparsers.add_parser("setup", help="Launch the interactive setup wizard")
    parser_setup.set_defaults(func=handle_setup)

    # 2. Run Command
    parser_run = subparsers.add_parser("run", help="Run a benchmark experiment")
    parser_run.add_argument(
        "app_config_file", help="Path to the JSON benchmark config."
    ).completer = FilesCompleter(allowednames=(".json",))
    parser_run.add_argument(
        "-p", "--preset", help="Name of the preset to use."
    ).completer = _preset_completer
    parser_run.add_argument("--log-level", dest="log_level", default=None, help="Log verbosity.")
    parser_run.add_argument(
        "--json", action="store_true", help="Print the submit result as JSON (logs go to stderr)."
    )
    parser_run.set_defaults(func=handle_run)

    # 3. TUI Command
    parser_tui = subparsers.add_parser("tui", help="Launch the Terminal User Interface")
    parser_tui.set_defaults(func=handle_tui)

    # 4. Web Command
    parser_web = subparsers.add_parser("web", help="Launch the local web dashboard")
    parser_web.add_argument("--host", default=None, help="Bind host (default: 127.0.0.1).")
    parser_web.add_argument("--port", type=int, default=None, help="Bind port (default: 8765).")
    parser_web.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser on start."
    )
    parser_web.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging (startup + per-request access logs).",
    )
    parser_web.set_defaults(func=handle_web)

    # 5. Introspection commands (machine-readable seam for the web dashboard)
    def _add_json_flag(p):
        p.add_argument(
            "--json", action="store_true", help="Emit machine-readable JSON instead of text."
        )
        return p

    parser_info = subparsers.add_parser("info", help="Show version, root, and available presets")
    _add_json_flag(parser_info).set_defaults(func=handle_info)

    parser_lb = subparsers.add_parser(
        "list-benchmarks", help="List installed benchmarks and discovered wrappers"
    )
    _add_json_flag(parser_lb).set_defaults(func=handle_list_benchmarks)

    parser_nodes = subparsers.add_parser(
        "nodes", help="Show Slurm partitions and nodes (via sinfo)"
    )
    _add_json_flag(parser_nodes).set_defaults(func=handle_nodes)

    parser_status = subparsers.add_parser("status", help="Show the state of Slurm job ids")
    parser_status.add_argument("job_ids", nargs="*", help="Slurm job ids to query.")
    _add_json_flag(parser_status).set_defaults(func=handle_status)

    parser_history = subparsers.add_parser(
        "history", help="List past experiments from metadata.csv"
    )
    parser_history.add_argument("-s", "--system", default=None, help="Limit to one system.")
    _add_json_flag(parser_history).set_defaults(func=handle_history)

    # 6. Export Command
    parser_export = subparsers.add_parser(
        "export", help="Export results as a self-contained HTML dashboard"
    )
    parser_export.add_argument(
        "data_dir", help="Path to the directory containing experiment results."
    ).completer = FilesCompleter()
    parser_export.add_argument(
        "-o", "--output", default=None, help="Output HTML file (default: crab_export.html)"
    )
    parser_export.set_defaults(func=handle_export)

    # 7. Worker Command (Hidden)
    parser_worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    parser_worker.add_argument("--workdir", required=True)
    parser_worker.add_argument("--log-level", dest="log_level", default=None)
    parser_worker.set_defaults(func=handle_worker)

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    args.func(args)
