"""Loading presets: the shipped `config/presets.json` plus the per-machine `local/presets.json`.

`local/` is git-ignored, so accounts and cluster tweaks written there survive `git pull`
without conflicts. Merge rules: a local preset replaces the shipped preset of the same name
as a whole; for `_common`, `env` merges key by key and any other key replaces the shipped one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SHIPPED_PRESETS = Path("config") / "presets.json"
LOCAL_PRESETS = Path("local") / "presets.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of presets.")
    return data


def load_all_presets(crab_root: Path) -> dict[str, Any]:
    """All presets for this checkout, with `local/presets.json` merged over the shipped file.

    Raises:
        FileNotFoundError: if the shipped presets file is missing.
        ValueError: if either file is not a JSON object.
    """
    presets = _read(crab_root / SHIPPED_PRESETS)
    local_file = crab_root / LOCAL_PRESETS
    if not local_file.exists():
        return presets

    for name, body in _read(local_file).items():
        if name == "_common" and isinstance(body, dict):
            common = dict(presets.get("_common", {}))
            env = {**common.get("env", {}), **body.get("env", {})}
            common.update({k: v for k, v in body.items() if k != "env"})
            common["env"] = env
            presets["_common"] = common
        else:
            presets[name] = body
    return presets
