import argparse
import argcomplete
from argcomplete.completers import FilesCompleter
import os
import json

def _preset_completer(prefix, parsed_args, **kwargs):
    """Dynamically parses presets.json for tab-autocompletion."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    presets_filename = os.path.join(base_dir, "config", "presets.json")
    try:
        with open(presets_filename, 'r') as f:
            all_presets = json.load(f)
        valid_presets = [k for k in all_presets.keys() if k not in ["_common", "example_preset"]]
        return [p for p in valid_presets if p.startswith(prefix)]
    except Exception:
        return []

def handle_run(args):
    from crab.cli.orchestrator import execute_orchestrator
    execute_orchestrator(args.app_config_file, args.preset, args.log_level)

def handle_setup(args):
    from crab.setup.wizard import run as run_wizard
    run_wizard()

def handle_tui(args):
    from crab.tui.app import BenchmarkApp
    app = BenchmarkApp()
    app.run()

def handle_worker(args):
    from crab.cli.orchestrator import execute_worker
    execute_worker(args.workdir, args.log_level)

def cli_router():
    parser = argparse.ArgumentParser(prog="crab", description="CRAB Benchmarking Framework")
    # 'metavar' is used to hide the "worker" entry
    subparsers = parser.add_subparsers(title="commands", dest="command", metavar="{setup,run,tui}")
    subparsers.required = True

    # 1. Setup Command
    parser_setup = subparsers.add_parser("setup", help="Launch the interactive setup wizard")
    parser_setup.set_defaults(func=handle_setup)

    # 2. Run Command
    parser_run = subparsers.add_parser("run", help="Run a benchmark experiment")
    parser_run.add_argument("app_config_file", help="Path to the JSON benchmark config.").completer = FilesCompleter(allowednames=('.json',))
    parser_run.add_argument("-p", "--preset", help="Name of the preset to use.").completer = _preset_completer
    parser_run.add_argument("--log-level", dest="log_level", default=None, help="Log verbosity.")
    parser_run.set_defaults(func=handle_run)

    # 3. TUI Command
    parser_tui = subparsers.add_parser("tui", help="Launch the Terminal User Interface")
    parser_tui.set_defaults(func=handle_tui)

    # 4. Worker Command (Hidden)
    parser_worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    parser_worker.add_argument("--workdir", required=True)
    parser_worker.add_argument("--log-level", dest="log_level", default=None)
    parser_worker.set_defaults(func=handle_worker)

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    args.func(args)
