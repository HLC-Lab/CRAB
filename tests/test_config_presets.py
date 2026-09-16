"""Regression coverage for the real repo config/presets.json (not a fixture).

The `"local"` preset is the trigger for CRAB_SCHEDULER=local (plan 087, dev/testing-only
no-Slurm path) — this guards against a future edit silently dropping the two env vars that
make it work.
"""

import json
from pathlib import Path

_PRESETS_PATH = Path(__file__).resolve().parents[1] / "config" / "presets.json"


def test_local_preset_triggers_the_local_scheduler():
    presets = json.loads(_PRESETS_PATH.read_text())
    local_env = presets["local"]["env"]
    assert local_env["CRAB_SCHEDULER"] == "local"
    assert local_env["CRAB_WL_MANAGER"] == "local"
