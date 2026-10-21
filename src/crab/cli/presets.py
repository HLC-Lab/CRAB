"""Loading presets: the shipped file, the user's file and the checkout's `local/presets.json`.

Three layers, each merged over the one before: the presets that ship inside the package
(`paths.shipped_presets()`), the user's file (`paths.user_presets()`), then `<root>/local/presets.json`.
`local/` is git-ignored, so accounts and cluster tweaks written in the user or local file survive
`git pull` without conflicts. Merge rules, per layer: a preset replaces the earlier preset of the same
name as a whole; for `_common`, `env` merges key by key and any other key replaces the earlier one.
"""

from __future__ import annotations

import json
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
