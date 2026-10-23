"""`crab presets show <name>`: a resolved preset, where each value came from and what is unset."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from crab import paths
from crab.cli.preset_marks import KEYED_ITEM, override_snippet, unset_marks
from crab.cli.presets import (
    PresetFile,
    explain_preset,
    preset_layers,
    preset_not_found_message,
    preset_sources,
)

Source = str | list[str]
"""The file a value came from; a list of files when several built a list value."""

Holds = Callable[[Mapping[str, Any]], bool]


def _last(chain: Sequence[PresetFile], holds: Holds) -> str | None:
    """The file of the last chain entry whose body satisfies `holds`."""
    files = [str(path) for path, body in chain if holds(body)]
    return files[-1] if files else None


def _contributors(chain: Sequence[PresetFile], holds: Holds) -> Source | None:
    """The files of every chain entry whose body satisfies `holds`: a string when there is one."""
    files = list(dict.fromkeys(str(path) for path, body in chain if holds(body)))
    if not files:
        return None
    return files[0] if len(files) == 1 else files


def _env_holds(key: str) -> Holds:
    return lambda body: isinstance(body.get("env"), Mapping) and key in body["env"]


def _key_holds(key: str) -> Holds:
    return lambda body: key in body


def _flags_of(body: Mapping[str, Any], kind: str | None) -> list[Any]:
    """The `sbatch` flag list of a body, or the `flags` of its launcher `kind`."""
    if kind is None:
        flags = body.get("sbatch")
    else:
        launchers = body.get("launchers")
        launcher = launchers.get(kind) if isinstance(launchers, Mapping) else None
        flags = launcher.get("flags") if isinstance(launcher, Mapping) else None
    return flags if isinstance(flags, list) else []


def _flag_holds(prefix: str, kind: str | None) -> Holds:
    """Entries whose flag list has an item starting with `prefix` (`--name=`)."""
    return lambda body: any(
        isinstance(entry, str) and entry.startswith(prefix) for entry in _flags_of(body, kind)
    )


def _flag_sources(
    label: str, kind: str | None, flags: Sequence[Any], chain: Sequence[PresetFile]
) -> dict[str, Source]:
    """`<label> --name` per keyed item (the last entry holding that name), `<label>` for the rest."""
    result: dict[str, Source] = {}
    unkeyed = False
    for item in flags:
        match = KEYED_ITEM.match(item) if isinstance(item, str) else None
        if match is None:
            unkeyed = True
            continue
        prefix = match.group(0)
        source = _last(chain, _flag_holds(prefix, kind))
        if source is not None:
            result[f"{label} {prefix[:-1]}"] = source
    if unkeyed:
        source = _contributors(
            chain,
            lambda body: any(
                not (isinstance(entry, str) and KEYED_ITEM.match(entry))
                for entry in _flags_of(body, kind)
            ),
        )
        if source is not None:
            result[label] = source
    return result


def _launcher_sources(
    kind: str, launcher: Mapping[str, Any], chain: Sequence[PresetFile]
) -> dict[str, Source]:
    def holds(field: str) -> Holds:
        def check(body: Mapping[str, Any]) -> bool:
            launchers = body.get("launchers")
            entry = launchers.get(kind) if isinstance(launchers, Mapping) else None
            return isinstance(entry, Mapping) and field in entry

        return check

    result: dict[str, Source] = {}
    for field, value in launcher.items():
        label = f"launchers.{kind}.{field}"
        if field == "flags" and isinstance(value, list):
            result.update(_flag_sources(label, kind, value, chain))
            continue
        source = (
            _contributors(chain, holds(field))
            if field == "export" and isinstance(value, list)
            else _last(chain, holds(field))
        )
        if source is not None:
            result[label] = source
    return result


def _attribute(resolved: Mapping[str, Any], chain: Sequence[PresetFile]) -> dict[str, Source]:
    """Where each value of a resolved preset came from, in the preset's field order.

    Keys: a top-level field (`description`), `env.<KEY>`, `sbatch --<name>`,
    `launchers.<kind>.<field>` and `launchers.<kind>.flags --<name>`. A value that can only be told
    apart as a list (`header`, `notes`, `export`, flag items without `--name=`) is attributed to
    every file that contributed to the list, under the list's own key.
    """
    result: dict[str, Source] = {}
    for key, value in resolved.items():
        if key == "env" and isinstance(value, Mapping):
            for env_key in value:
                source = _last(chain, _env_holds(env_key))
                if source is not None:
                    result[f"env.{env_key}"] = source
        elif key == "sbatch" and isinstance(value, list):
            result.update(_flag_sources("sbatch", None, value, chain))
        elif key == "launchers" and isinstance(value, Mapping):
            for kind, launcher in value.items():
                if isinstance(launcher, Mapping):
                    result.update(_launcher_sources(kind, launcher, chain))
        else:
            source = (
                _contributors(chain, _key_holds(key))
                if key in ("header", "notes") and isinstance(value, list)
                else _last(chain, _key_holds(key))
            )
            if source is not None:
                result[key] = source
    return result


def show(crab_root: Path, name: str) -> dict[str, Any]:
    """The resolved preset `name` and its history, ready for `--json`.

    Keys: `name`, `preset`, `sources` (see `_attribute`; paths are strings), `extends` (the names
    it extends, nearest first), `needs` (the unset `<YOUR_...>` labels), `notes`, `override` (the
    `path` of the file to write and the `snippet` to put in it).

    Raises:
        KeyError: no preset has that name.
        FileNotFoundError: the package presets file is missing.
        ValueError: the preset files are rejected; the message names the file.
    """
    shared, overrides = preset_layers(crab_root)
    resolved, chain, parents = explain_preset(shared, overrides, name)
    overrides_dir = preset_sources(crab_root)[2]
    notes = resolved.get("notes")
    return {
        "name": name,
        "preset": resolved,
        "sources": _attribute(resolved, chain),
        "extends": parents,
        "needs": unset_marks(resolved),
        "notes": notes if isinstance(notes, list) else [],
        "override": {
            "path": str(overrides_dir / f"{name}.json"),
            "snippet": override_snippet(name, resolved),
        },
    }


def _from(source: Source | None) -> str:
    if source is None:
        return ""
    return f"  (from {source if isinstance(source, str) else ', '.join(source)})"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ", ".join(value)
    return json.dumps(value)


def _print_items(
    indent: str, label: str, items: Sequence[Any], sources: Mapping[str, Source]
) -> None:
    """A list field: its label with the list's source, then one item per line.

    A `--name=value` item shows the source of `<label> --name`.
    """
    print(f"{indent}{label.rsplit('.', 1)[-1]}:{_from(sources.get(label))}")
    for item in items:
        match = KEYED_ITEM.match(item) if isinstance(item, str) else None
        source = sources.get(f"{label} {match.group(0)[:-1]}") if match is not None else None
        print(f"{indent}  {_text(item)}{_from(source)}")


def _print_launchers(launchers: Mapping[str, Any], sources: Mapping[str, Source]) -> None:
    print("  launchers:")
    for kind, launcher in launchers.items():
        print(f"    {kind}:")
        if not isinstance(launcher, Mapping):
            print(f"      {_text(launcher)}")
            continue
        for field, value in launcher.items():
            label = f"launchers.{kind}.{field}"
            if field == "flags" and isinstance(value, list):
                _print_items("      ", label, value, sources)
            else:
                print(f"      {field}: {_text(value)}{_from(sources.get(label))}")


def _print_human(data: Mapping[str, Any]) -> None:
    preset, sources = data["preset"], data["sources"]
    extends = f"  (extends {', '.join(data['extends'])})" if data["extends"] else ""
    print(f"{data['name']}{extends}")
    for key, value in preset.items():
        if key == "notes":
            continue
        if key == "env" and isinstance(value, Mapping):
            print("  env:")
            for env_key, env_value in value.items():
                print(f"    {env_key}={_text(env_value)}{_from(sources.get(f'env.{env_key}'))}")
        elif key == "sbatch" and isinstance(value, list):
            _print_items("  ", "sbatch", value, sources)
        elif key == "header" and isinstance(value, list):
            _print_items("  ", "header", value, sources)
        elif key == "launchers" and isinstance(value, Mapping):
            _print_launchers(value, sources)
        else:
            print(f"  {key}: {_text(value)}{_from(sources.get(key))}")
    if data["notes"]:
        print(f"notes:{_from(sources.get('notes'))}")
        for note in data["notes"]:
            print(f"  - {_text(note)}")
    override = data["override"]
    if data["needs"]:
        print(f"needs setup: {', '.join(data['needs'])}")
        print(f"To set your values, write {override['path']}:")
        print(json.dumps(override["snippet"], indent=2))
    else:
        print("needs setup: nothing")
        print(
            f"Personal tweaks go in {override['path']} "
            "(a preset with this name patches the one above)."
        )


def handle_presets_show(args: argparse.Namespace) -> None:
    from crab.cli import contract

    root = paths.crab_home()
    try:
        data = show(root, args.name)
    except KeyError:
        message = preset_not_found_message(args.name, root)
    except (ValueError, FileNotFoundError) as exc:
        message = str(exc)
    else:
        contract.emit({"schema": contract.CONTRACT_SCHEMA, **data}, args.json, _print_human)
        return
    if args.json:
        print(json.dumps({"schema": contract.CONTRACT_SCHEMA, "error": message}, indent=2))
    else:
        print(f"crab: {message}", file=sys.stderr)
    sys.exit(1)


def register(subparsers: Any, preset_completer: Any = None) -> None:
    parser = subparsers.add_parser("presets", help="Inspect presets")
    sub = parser.add_subparsers(dest="presets_command", required=True)
    show_parser = sub.add_parser(
        "show", help="Explain one preset: where each value comes from, what is unset"
    )
    name = show_parser.add_argument("name", help="Preset name.")
    if preset_completer is not None:
        name.completer = preset_completer
    show_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of text."
    )
    show_parser.set_defaults(func=handle_presets_show)
