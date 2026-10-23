"""Loading presets: the shared folders, the package file and the user's override files.

The shared layers come first: `presets/*.json` in each shared wrapper folder (`CRAB_PATH_WRAPPERS`
entries, else `<root>/wrappers`; the first folder wins a name), then the package's own
`presets.json` as the lowest layer. The user's override layer is every `*.json` in
`paths.user_presets_dir()`. `resolve_presets` below merges the layers; the two single-file overrides
CRAB read before 1.2 are refused.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from crab import paths


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of presets.")
    return data


def shared_preset_folders(crab_root: Path) -> list[Path]:
    """The shared presets folders in search order: `<wrapper folder>/presets` for each folder.

    The wrapper folders are the non-empty `CRAB_PATH_WRAPPERS` entries, else `<crab_root>/wrappers`.
    """
    entries = [
        entry for entry in os.environ.get("CRAB_PATH_WRAPPERS", "").split(os.pathsep) if entry
    ]
    if entries:
        return [Path(entry) / "presets" for entry in entries]
    return [Path(crab_root) / "wrappers" / "presets"]


def preset_sources(crab_root: Path) -> tuple[list[Path], Path, Path]:
    """Where presets are read from: the shared folders, the package file and the overrides dir."""
    return (
        shared_preset_folders(crab_root),
        paths.shipped_presets(),
        paths.user_presets_dir(Path(crab_root)),
    )


def _read_folder(folder: Path) -> list[PresetFile]:
    """The `*.json` files of a presets folder in name order; a missing folder is an empty layer."""
    if not folder.is_dir():
        return []
    return [(file, _read(file)) for file in sorted(folder.glob("*.json"))]


def load_all_presets(crab_root: Path) -> dict[str, Any]:
    """All presets, resolved: shared folders, then the package file, patched by the override files.

    Raises:
        FileNotFoundError: if the package presets file is missing.
        ValueError: an old single-file override exists, a preset file is not a JSON object (the
            message names the file) or `resolve_presets` rejects the layers.
    """
    root = Path(crab_root)
    overrides_dir = paths.user_presets_dir(root)
    for old in paths.old_preset_files(root):
        if old.exists():
            raise ValueError(
                f"{old}: single-file preset overrides were removed in CRAB 1.2. Move your presets "
                f"into {overrides_dir}/ (any *.json name); a preset with a shared name now patches "
                "it instead of replacing it. See the Presets page of the docs."
            )
    shared = [_read_folder(folder) for folder in shared_preset_folders(root)]
    package = paths.shipped_presets()
    shared.append([(package, _read(package))])
    return resolve_presets(shared, _read_folder(overrides_dir))


PresetFile = tuple[Path, Mapping[str, Any]]
"""One preset file: its path (for error messages) and its parsed JSON object."""

_KEYED_ITEM = re.compile(r"--[^=\s]+=")


def _merge_flags(base: list[Any], patch: list[Any]) -> list[Any]:
    """`--name=value` items replace the first base item with the same `--name=`; others append."""
    result = list(base)
    for item in patch:
        match = _KEYED_ITEM.match(item) if isinstance(item, str) else None
        if match is not None:
            prefix = match.group(0)
            for index, existing in enumerate(result):
                if isinstance(existing, str) and existing.startswith(prefix):
                    result[index] = item
                    break
            else:
                result.append(item)
        else:
            result.append(item)
    return result


def _merge_env(base: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return patch
    result = dict(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def _merge_union(base: list[Any], patch: list[Any]) -> list[Any]:
    result = list(base)
    for item in patch:
        if item not in result:
            result.append(item)
    return result


def _merge_launcher(base: Any, patch: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    result = dict(base)
    for key, value in patch.items():
        if key == "flags" and isinstance(base.get(key, []), list) and isinstance(value, list):
            result[key] = _merge_flags(base.get(key, []), value)
        elif key == "export" and isinstance(base.get(key), list) and isinstance(value, list):
            result[key] = _merge_union(base[key], value)
        else:
            result[key] = value
    return result


def _merge_launchers(base: Any, patch: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    result = dict(base)
    for kind, value in patch.items():
        result[kind] = _merge_launcher(base.get(kind, {}), value)
    return result


def _patch_preset(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """`base` patched with `patch` by ADR-041's merge rules; neither input is changed."""
    result = copy.deepcopy(dict(base))
    for key, raw in patch.items():
        value = copy.deepcopy(raw)
        current = result.get(key)
        if key == "env":
            result[key] = _merge_env(current, value)
        elif key == "sbatch" and isinstance(result.get(key, []), list) and isinstance(value, list):
            result[key] = _merge_flags(result.get(key, []), value)
        elif key == "header" and isinstance(current, list) and isinstance(value, list):
            result[key] = current + value
        elif key == "notes" and isinstance(current, list) and isinstance(value, list):
            result[key] = _merge_union(current, value)
        elif key == "launchers":
            result[key] = _merge_launchers(result.get(key, {}), value)
        else:
            result[key] = value
    return result


def _complete_preset(body: Mapping[str, Any]) -> dict[str, Any]:
    """A preset that patches nothing: a copy with the `null` env values dropped."""
    return _patch_preset({}, body)


class _Resolver:
    """Resolves each preset name once, following `extends` with cycle detection."""

    def __init__(
        self,
        shared_layers: Sequence[Sequence[PresetFile]],
        override_layer: Sequence[PresetFile],
    ) -> None:
        self.shared: dict[str, PresetFile] = {}
        for layer in shared_layers:
            # The first folder wins: names a folder shares with an earlier one are ignored whole.
            for name, entry in self._index(layer).items():
                self.shared.setdefault(name, entry)
        self.overrides = self._index(override_layer)
        self.done: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _index(layer: Sequence[PresetFile]) -> dict[str, PresetFile]:
        """name -> (file, body) for one layer; a name in two files is an error."""
        names: dict[str, PresetFile] = {}
        for path, data in layer:
            if not isinstance(data, Mapping):
                raise ValueError(f"{path}: must contain a JSON object of presets.")
            for name, body in data.items():
                if name == "_common":
                    raise ValueError(
                        f'{path}: "_common" was removed in CRAB 1.2; put its env in each preset.'
                    )
                if not isinstance(body, Mapping):
                    raise ValueError(f"{path}: preset {name!r} must be an object.")
                if name in names:
                    raise ValueError(
                        f"preset {name!r} is defined twice: {names[name][0]} and {path}."
                    )
                names[name] = (path, body)
        return names

    def names(self) -> list[str]:
        return list(self.shared) + [name for name in self.overrides if name not in self.shared]

    def resolve(self, name: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
        if name in self.done:
            return self.done[name]
        if name in stack:
            cycle = " -> ".join((*stack[stack.index(name) :], name))
            raise ValueError(f"{self._file_of(name)}: preset {name!r}: extends cycle {cycle}.")
        stack = (*stack, name)
        shared = self.shared.get(name)
        override = self.overrides.get(name)
        if shared is not None:
            effective = self._definition(name, shared, stack)
            if override is not None:
                path, body = override
                if "extends" in body:
                    raise ValueError(
                        f"{path}: preset {name!r} patches the shared preset {name!r} and cannot "
                        'set "extends"; write a new preset under another name.'
                    )
                effective = _patch_preset(effective, body)
        elif override is not None:
            path, body = override
            if "extends" not in body and "scheduler" not in body:
                raise ValueError(
                    f"{path}: preset {name!r} has no shared preset to patch. Run `crab update` to "
                    'fetch crab-wrappers, or make it a complete preset with "scheduler".'
                )
            effective = self._definition(name, override, stack)
        else:
            raise ValueError(f"preset {name!r} does not exist.")
        self.done[name] = effective
        return effective

    def _file_of(self, name: str) -> Path:
        entry = self.shared.get(name) or self.overrides[name]
        return entry[0]

    def _definition(self, name: str, entry: PresetFile, stack: tuple[str, ...]) -> dict[str, Any]:
        """The preset an entry defines: its parent's effective preset patched with it, or itself."""
        path, body = entry
        if "extends" not in body:
            return _complete_preset(body)
        parent = body["extends"]
        if not isinstance(parent, str) or (
            parent not in self.shared and parent not in self.overrides
        ):
            raise ValueError(f"{path}: preset {name!r} extends {parent!r}, which does not exist.")
        own = {key: value for key, value in body.items() if key != "extends"}
        return _patch_preset(self.resolve(parent, stack), own)


def resolve_presets(
    shared_layers: Sequence[Sequence[PresetFile]], override_layer: Sequence[PresetFile]
) -> dict[str, dict[str, Any]]:
    """The final presets from the shared folders' preset files and the user's override files.

    `shared_layers` has one entry per shared folder in search order (the first folder wins a name),
    each holding that folder's files in name order. A user preset with a shared name patches it; one
    with `"extends": "<name>"` is a variant of that preset's effective form; any other new name must
    be complete (have a `scheduler`). Merge rules are ADR-041's. The result never holds `extends`.

    Raises:
        ValueError: a preset is defined twice in one layer, has no base, extends a missing name or
            starts a cycle; the message names the file.
    """
    resolver = _Resolver(shared_layers, override_layer)
    return {name: resolver.resolve(name) for name in resolver.names()}
