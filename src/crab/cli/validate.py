"""`crab validate`: the config checks `crab run` makes before it submits, without submitting.

`check_config_problems` is also what the web dashboard runs when it saves a config, so one gate
covers every producer of configs. It holds only checks that need no files of this machine: the
dashboard runs it on a laptop where the cluster's wrappers are absent. `validate_file` adds the
checks that read this machine's wrapper search path (every app's wrapper `path` must name an
existing file), because `crab validate` runs where `crab run` will.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def check_config_problems(config: Any, settings: Any = None) -> tuple[list[str], list[str]]:
    """Run the engine's pre-submit checks on a parsed config: (errors, warnings).

    The order is the engine's: the schema version first (an error stops there), then
    `check_config`, which raises on the first bad value and returns warnings otherwise. With
    `settings` (an `ExecutionSettings`) the config is also checked against what the preset can
    run.
    """
    from crab.core.config_checks import check_config
    from crab.core.engine import check_config_schema_version

    if not isinstance(config, dict):
        return ["config is not a JSON object"], []
    try:
        check_config_schema_version(config)
        return [], check_config(config, settings)
    except ValueError as error:
        return [str(error)], []
    except (AttributeError, KeyError, TypeError) as error:
        # A shape the checks do not expect (e.g. `experiments` as a list): not a rule of theirs.
        return [f"config could not be checked: {type(error).__name__}: {error}"], []


def _load_config(path: str) -> dict[str, Any] | str:
    """The parsed config file, or a one-line error message."""
    try:
        with open(path) as handle:
            return json.load(handle)
    except json.JSONDecodeError as error:
        return f"{path}: not valid JSON: {error}"
    except OSError as error:
        return f"cannot read {path}: {error.strerror or error}"


def _preset_settings(config: Any, preset: str) -> tuple[Any, dict[str, str]]:
    """The `ExecutionSettings` and the execution environment `crab run -p` would build.

    Also injects the preset's sbatch directives into the config as `crab run` does, because the
    managed-directive check reads them there.

    Raises:
        KeyError, ValueError: from `load_environment_config`.
    """
    from crab.cli import orchestrator

    loaded = orchestrator.load_environment_config(preset)
    options = config.get("global_options") if isinstance(config, dict) else None
    if isinstance(options, dict) and "system_sbatch" not in options:
        options["system_sbatch"] = loaded["sbatch"]
    return loaded["settings"], orchestrator.prepare_execution_environment(loaded["env"])


def validate_file(path: str, preset: str | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a config file, optionally against a preset.

    Besides `check_config_problems`, every app's wrapper path must resolve on the wrapper search
    path (the preset's `CRAB_PATH_WRAPPERS`, when it sets one, as in `crab run -p`); a known
    pre-rename Blink name gets a message saying what replaced it.
    """
    config = _load_config(path)
    if isinstance(config, str):
        return [config], []
    settings = None
    execution_env: dict[str, str] = {}
    if preset:
        try:
            settings, execution_env = _preset_settings(config, preset)
        except KeyError as error:
            return [str(error.args[0])], []
        except ValueError as error:
            return [str(error)], []
    errors, warnings = check_config_problems(config, settings)
    if isinstance(config, dict):
        from crab.core.wrapper_paths import config_wrapper_path_problems

        errors = errors + config_wrapper_path_problems(config, env=execution_env)
    return errors, warnings


def handle_validate(args: Any) -> None:
    errors, warnings = validate_file(args.config_file, args.preset)
    ok = not errors
    if args.json:
        print(json.dumps({"ok": ok, "errors": errors, "warnings": warnings}, indent=2))
    elif ok:
        print(f"ok: {args.config_file}")
        for warning in warnings:
            print(warning)
    else:
        for error in errors:
            print(f"crab: {error}", file=sys.stderr)
    if not ok:
        sys.exit(1)


def register(subparsers: Any, preset_completer: Any = None) -> None:
    from argcomplete.completers import FilesCompleter

    parser = subparsers.add_parser(
        "validate", help="Check a benchmark config the way `crab run` does, without running it"
    )
    parser.add_argument(
        "config_file", help="Path to the JSON benchmark config."
    ).completer = FilesCompleter(allowednames=(".json",))
    preset = parser.add_argument(
        "-p",
        "--preset",
        default=None,
        help="Also check the config against this preset (hosts, launcher, scheduler).",
    )
    if preset_completer is not None:
        preset.completer = preset_completer
    parser.add_argument(
        "--json",
        action="store_true",
        help='Print {"ok", "errors", "warnings"} as JSON instead of text.',
    )
    parser.set_defaults(func=handle_validate)
