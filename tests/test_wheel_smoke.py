"""The wheel smoke test's own files: a fast check that they stay well-formed.

The full run (`scripts/wheel_smoke.sh <wheel>`) needs a built wheel and network, so CI runs it in
the `wheel` job; here the script is only syntax-checked and its config checked by the engine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from crab.cli.validate import check_config_problems
from settings_fixtures import LOCAL_DIRECT

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_wheel_smoke_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPTS / "wheel_smoke.sh")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_wheel_smoke_config_passes_the_engines_checks():
    config = json.loads((SCRIPTS / "wheel_smoke" / "smoke.json").read_text())
    errors, warnings = check_config_problems(config, LOCAL_DIRECT)
    assert errors == []
    assert warnings == []
