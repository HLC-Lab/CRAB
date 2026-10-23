"""Loading presets: the shipped file, the user's file and the checkout's `local/presets.json`.

Three layers, each merged over the one before: the presets that ship inside the package
(`paths.shipped_presets()`), the user's file (`paths.user_presets()`), then `<root>/local/presets.json`.
`local/` is git-ignored, so accounts and cluster tweaks written in the user or local file survive
`git pull` without conflicts. Merge rules, per layer: a preset replaces the earlier preset of the same
name as a whole; for `_common`, `env` merges key by key and any other key replaces the earlier one.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from crab import paths

LOCAL_PRESETS = Path("local") / "presets.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of presets.")
    return data


def preset_locations(crab_root: Path) -> tuple[Path, Path, Path]:
    """The shipped, user and local presets files, in merge order."""
    return paths.shipped_presets(), paths.user_presets(), Path(crab_root) / LOCAL_PRESETS


def _merge(presets: dict[str, Any], layer: dict[str, Any]) -> None:
    for name, body in layer.items():
        if name == "_common" and isinstance(body, dict):
            common = dict(presets.get("_common", {}))
            env = {**common.get("env", {}), **body.get("env", {})}
            common.update({k: v for k, v in body.items() if k != "env"})
            common["env"] = env
            presets["_common"] = common
        else:
            presets[name] = body


def load_all_presets(crab_root: Path) -> dict[str, Any]:
    """All presets: the shipped file, then the user file, then `<crab_root>/local/presets.json`.

    Raises:
        FileNotFoundError: if the shipped presets file is missing.
        ValueError: if any file that exists is not a JSON object (the message names the file).
    """
    shipped, user, local = preset_locations(crab_root)
    presets = _read(shipped)
    for layer_file in (user, local):
        if layer_file.exists():
            _merge(presets, _read(layer_file))
    return presets


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
