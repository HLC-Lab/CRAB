"""config.json carries `schema_version` so later CRAB releases can migrate old configs
instead of misreading them. Missing means 1 (every config written before the field existed)."""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.engine import CONFIG_SCHEMA_VERSION, Engine


def _run(config: dict) -> None:
    Engine(logger=MagicMock()).run(config=config, environment={}, is_worker=False)


@pytest.mark.parametrize("version", [3, 99])
def test_newer_schema_version_is_refused_with_an_update_hint(version: int) -> None:
    config = {"schema_version": version, "global_options": {}, "experiments": {}}
    with pytest.raises(ValueError, match=r"schema_version .*crab update"):
        _run(config)


@pytest.mark.parametrize("version", ["1", 1.5, True, None, 0])
def test_non_integer_schema_version_is_refused(version: object) -> None:
    config = {"schema_version": version, "global_options": {}, "experiments": {}}
    with pytest.raises(ValueError, match="schema_version must be"):
        _run(config)


@pytest.mark.parametrize("extra", [{}, {"schema_version": 1}, {"schema_version": 2}])
def test_missing_or_supported_version_reaches_normal_validation(extra: dict) -> None:
    # No numnodes: the engine's own "numnodes is required" error proves the version gate let it through.
    config = {**extra, "global_options": {}, "experiments": {"e": {"apps": {}}}}
    with pytest.raises(ValueError, match="numnodes is required"):
        _run(config)


def test_dashboard_emits_the_engine_config_version() -> None:
    config_ts = Path(__file__).parents[1] / "src/crab/webui/src/lib/config.ts"
    match = re.search(r"export const CONFIG_SCHEMA_VERSION = (\d+);", config_ts.read_text())
    assert match and int(match.group(1)) == CONFIG_SCHEMA_VERSION
