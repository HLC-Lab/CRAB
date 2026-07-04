"""Config shape model (plan 020 / ADR-015): every real example config must
validate warning-free (they are ground truth for what the engine accepts), and
genuinely malformed shapes must produce warnings — never exceptions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.models import validate_config  # noqa: E402

_EXAMPLES = sorted((Path(__file__).parent.parent / "examples").rglob("*.json"))
assert _EXAMPLES, "examples/ configs are the ground truth for this suite"


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.name)
def test_every_example_validates_warning_free(path: Path):
    config = json.loads(path.read_text())
    assert validate_config(config) == []


def test_wrong_types_warn_with_locations():
    warnings = validate_config(
        {
            "global_options": {"numnodes": ["not", "a", "number"], "convergeall": "maybe"},
            "experiments": {"e": {"apps": {"0": {"collect": "definitely"}}}},
        }
    )
    assert any("numnodes" in w for w in warnings)
    assert any("convergeall" in w for w in warnings)
    assert any("collect" in w for w in warnings)


def test_structural_nonsense_warns_not_raises():
    assert validate_config("just a string") == ["config is not a JSON object"]
    assert validate_config({"experiments": "nope"})  # non-dict experiments → warning


def test_unknown_keys_are_features_not_warnings():
    # Wrapper attributes on apps and future engine options must pass silently.
    config = {
        "global_options": {"numnodes": "4", "future_option": {"x": 1}},
        "experiments": {"e": {"apps": {"0": {"path": "a.py", "msgsize": 8192, "warmup": True}}}},
    }
    assert validate_config(config) == []


def test_legacy_applications_form_is_accepted():
    config = {
        "global_options": {"numnodes": "2"},
        "applications": {"0": {"path": "a.py", "args": ""}},
    }
    assert validate_config(config) == []
