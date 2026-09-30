"""Config values the engine used to ignore or misread are errors before anything runs.

Before: `"convergeall": "false"` was truthy (runner.py `bool(...)`), an unknown `outformat`
wrote no data (data/utils.py only handled csv/hdf), a typo in `allocation.mode` fell back to
linear (runner.py/allocator.py `else` branches), and an app without `path` was skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.config_checks import parse_bool
from crab.core.engine import Engine

REPO = Path(__file__).resolve().parents[1]


def _run(global_options: dict | None = None, local: dict | None = None, apps: dict | None = None):
    exp: dict = {"apps": apps if apps is not None else {"0": {"path": "a.py"}}}
    if local is not None:
        exp["local_options"] = local
    config = {"global_options": global_options or {}, "experiments": {"e1": exp}}
    Engine(logger=MagicMock()).run(config=config, environment={}, is_worker=False)


def _passes_checks(**kwargs) -> None:
    # No numnodes: reaching the engine's own "numnodes is required" means the checks passed.
    with pytest.raises(ValueError, match="numnodes is required"):
        _run(**kwargs)


@pytest.mark.parametrize("key", ["convergeall", "retain_files"])
@pytest.mark.parametrize("value", ["yes", 1, "", None])
def test_option_booleans_must_be_true_or_false(key: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"e1.*{key}"):
        _run(global_options={key: value})


@pytest.mark.parametrize("value", [True, False, "true", "False"])
def test_option_booleans_accept_real_and_spelled_booleans(value: object) -> None:
    _passes_checks(global_options={"convergeall": value}, local={"retain_files": value})


def test_spelled_false_means_false() -> None:
    assert parse_bool("false", "convergeall") is False
    assert parse_bool("TRUE", "convergeall") is True


def test_app_collect_must_be_a_boolean() -> None:
    with pytest.raises(ValueError, match=r"e1.*app 0.*collect"):
        _run(apps={"0": {"path": "a.py", "collect": "maybe"}})


@pytest.mark.parametrize("fmt", ["hdf", "json", "CSV "])
def test_only_csv_output_is_accepted(fmt: str) -> None:
    with pytest.raises(ValueError, match=r"outformat.*csv"):
        _run(local={"outformat": fmt})


def test_unknown_allocation_mode_is_refused() -> None:
    with pytest.raises(
        ValueError, match=r"allocation\.mode 'interleave'.*linear, interleaved, random"
    ):
        _run(global_options={"allocation": {"mode": "interleave"}})


def test_unknown_partition_mode_is_refused() -> None:
    alloc = {"partitions": {"left": {"share": 50, "mode": "rnd"}, "right": {"share": 50}}}
    with pytest.raises(ValueError, match=r"partition 'left'.*'rnd'"):
        _run(global_options={"allocation": alloc})


@pytest.mark.parametrize("app", [{"args": "-x"}, {"path": ""}, {"path": "  "}])
def test_app_without_path_is_refused(app: dict) -> None:
    with pytest.raises(ValueError, match=r"e1.*app 0.*path"):
        _run(apps={"0": app})


def test_legacy_applications_form_is_checked_too() -> None:
    config = {"global_options": {}, "applications": {"0": {"args": "-x"}}}
    with pytest.raises(ValueError, match=r"app 0.*path"):
        Engine(logger=MagicMock()).run(config=config, environment={}, is_worker=False)


def test_every_shipped_example_passes_the_checks() -> None:
    from crab.core.config_checks import check_config

    failures = {}
    for f in sorted((REPO / "examples").rglob("*.json")):
        try:
            check_config(json.loads(f.read_text()))
        except ValueError as exc:
            failures[str(f.relative_to(REPO))] = str(exc)
    assert failures == {}
