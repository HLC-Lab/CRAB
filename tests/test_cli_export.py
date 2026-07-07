"""Characterization tests for cli/export.py's CSV tree walker and row parser.

No test file existed for this module before this plan (confirmed by search); these tests
pin the current behavior across the three directory shapes _collect_data handles, so the
_collect_data -> collect_result_data / _parse_csv -> parse_csv rename (plan 065 S2) is a
provably pure rename. Written first against the private names, then the imports below are
updated to the public names once the rename lands, with assertions left unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from crab.cli.export import collect_result_data, parse_csv


def test_parse_csv_coerces_numeric_columns(tmp_path: Path):
    path = tmp_path / "data_app_0.csv"
    path.write_text("name,count,ratio\nfoo,3,1.5\nbar,,2\n", encoding="utf-8")

    rows = parse_csv(path)

    assert rows == [
        {"name": "foo", "count": 3, "ratio": 1.5},
        {"name": "bar", "count": "", "ratio": 2},
    ]


def test_collect_result_data_handles_the_three_directory_shapes(tmp_path: Path):
    # Shape 1: CSVs directly at the root -> "Root Lab".
    (tmp_path / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    # System CSVs at the root are skipped.
    (tmp_path / "metadata.csv").write_text("k,v\na,b\n", encoding="utf-8")

    # Shape 2: one-level -- a dir directly under root holding CSVs.
    one_level = tmp_path / "solo-experiment"
    one_level.mkdir()
    (one_level / "data_app_1.csv").write_text("y\n2\n", encoding="utf-8")

    # Shape 3: two-level -- a lab dir whose subdirs are the experiment dirs.
    lab_dir = tmp_path / "lab-a"
    exp_dir = lab_dir / "exp-1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "data_app_2.csv").write_text("z\n3\n", encoding="utf-8")

    labs = collect_result_data(tmp_path)

    assert labs == {
        "Root Lab": {"App 0": [{"x": 1}]},
        "solo-experiment": {"App 1": [{"y": 2}]},
        "exp-1": {"App 2": [{"z": 3}]},
    }


def test_collect_result_data_skips_empty_csvs(tmp_path: Path):
    (tmp_path / "data_app_0.csv").write_text("x\n", encoding="utf-8")

    labs = collect_result_data(tmp_path)

    assert labs == {}


def _write_config(directory: Path, experiments: dict) -> None:
    (directory / "config.json").write_text(
        json.dumps({"experiments": experiments}), encoding="utf-8"
    )


def test_collect_result_data_resolves_app_names_from_config_json(tmp_path: Path):
    exp_dir = tmp_path / "exp-1"
    exp_dir.mkdir()
    (exp_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    _write_config(tmp_path, {"exp-1": {"apps": {"0": {"path": "/wrappers/blink.py"}}}})

    labs = collect_result_data(tmp_path)

    assert labs == {"exp-1": {"blink": [{"x": 1}]}}


def test_collect_result_data_falls_back_to_placeholder_when_config_missing(tmp_path: Path):
    exp_dir = tmp_path / "exp-1"
    exp_dir.mkdir()
    (exp_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    # No config.json written at all.

    labs = collect_result_data(tmp_path)

    assert labs == {"exp-1": {"App 0": [{"x": 1}]}}


def test_collect_result_data_falls_back_when_config_json_malformed(tmp_path: Path):
    exp_dir = tmp_path / "exp-1"
    exp_dir.mkdir()
    (exp_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")

    labs = collect_result_data(tmp_path)

    assert labs == {"exp-1": {"App 0": [{"x": 1}]}}


def test_collect_result_data_falls_back_when_experiment_or_app_key_missing(tmp_path: Path):
    exp_dir = tmp_path / "exp-1"
    exp_dir.mkdir()
    (exp_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    _write_config(tmp_path, {"other-exp": {"apps": {"0": {"path": "/wrappers/blink.py"}}}})

    labs = collect_result_data(tmp_path)

    assert labs == {"exp-1": {"App 0": [{"x": 1}]}}
