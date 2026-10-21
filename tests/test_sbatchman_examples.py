"""The SbatchMan example campaigns launch as they are: every embedded CRAB config passes the checks.

A job in `examples/sbatchman/*.yaml` writes its `config.json` from a heredoc in `preprocess`, with
SbatchMan variables such as `{nodes}` left in the text. These tests substitute every value of every
declared variable and run the engine's pre-submit checks on the result, so a typo in a campaign
shows up here and not on a cluster queue.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import requires_wrappers_checkout
from crab.cli.validate import check_config_problems
from crab.core.wrapper_paths import wrapper_path_problem

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "sbatchman"
FILES = sorted(EXAMPLES.glob("*.yaml"))
EXPECTED_FILES = {"smoke.yaml", "a2a-vs-incast.yaml", "a2a-vs-a2a.yaml"}
HEREDOC = re.compile(r"<<'JSON'\n(.*?)\nJSON\n", re.DOTALL)


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _jobs(path: Path) -> list[dict[str, Any]]:
    return _load(path)["jobs"]


def _combinations(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every assignment of the file's variables (their Cartesian product)."""
    variables = doc["variables"]
    names = list(variables)
    return [
        dict(zip(names, values, strict=True)) for values in itertools.product(*variables.values())
    ]


def _config_text(job: dict[str, Any]) -> str:
    match = HEREDOC.search(job["preprocess"])
    assert match, f"job {job.get('tag')}: no config.json heredoc in preprocess"
    return match.group(1)


def _config(job: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    text = _config_text(job)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return json.loads(text)


def _cases() -> list[Any]:
    return [
        pytest.param(path, job, values, id=f"{path.name}-{job['tag']}-{values}")
        for path in FILES
        for job in _jobs(path)
        for values in _combinations(_load(path))
    ]


def test_the_three_campaigns_are_shipped() -> None:
    assert {p.name for p in FILES} == EXPECTED_FILES


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_file_has_the_shape_the_dashboard_writes(path: Path) -> None:
    doc = _load(path)

    assert doc["configs"] == "configs.yaml"
    assert doc["variables"]["nodes"]
    assert doc["jobs"]
    for job in doc["jobs"]:
        assert job["config"] == "crab_run_{nodes}"
        assert "{nodes}" in job["tag"]
        assert HEREDOC.search(job["preprocess"])
        assert 'mkdir -p "$SBATCHMAN_JOB_DIR"' in job["preprocess"]
        assert 'cat > "$SBATCHMAN_JOB_DIR/config.json"' in job["preprocess"]
        assert job["command"] == 'crab worker --workdir "$SBATCHMAN_JOB_DIR"'


@pytest.mark.parametrize(("path", "job", "values"), _cases())
def test_every_embedded_config_passes_the_checks(
    path: Path, job: dict[str, Any], values: dict[str, Any]
) -> None:
    config = _config(job, values)

    errors, _warnings = check_config_problems(config)

    assert errors == []
    assert config["global_options"]["name"] == job["tag"].replace("{nodes}", str(values["nodes"]))


@requires_wrappers_checkout
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_app_path_exists_in_the_wrappers_checkout(path: Path) -> None:
    doc = _load(path)
    values = _combinations(doc)[0]
    for job in doc["jobs"]:
        for app in _config(job, values)["experiments"]["run"]["apps"].values():
            assert wrapper_path_problem(app["path"]) is None


@pytest.mark.parametrize("path", [p for p in FILES if p.name != "smoke.yaml"], ids=lambda p: p.name)
def test_baseline_and_noise_share_the_allocation_and_the_victim(path: Path) -> None:
    """The fairness rule: same 2N nodes, same placement, same victim; only the aggressor differs."""
    doc = _load(path)
    values = _combinations(doc)[0]
    baseline, noise = (_config(job, values) for job in doc["jobs"])
    base_run = baseline["experiments"]["run"]
    noise_run = noise["experiments"]["run"]

    assert _config_text(doc["jobs"][0]).count('"numnodes": "{nodes}"') == 1
    assert baseline["global_options"]["numnodes"] == noise["global_options"]["numnodes"]
    assert baseline["global_options"]["ppn"] == noise["global_options"]["ppn"]
    assert base_run["local_options"]["allocation"] == noise_run["local_options"]["allocation"]
    assert base_run["local_options"]["allocation"]["mode"] == "interleaved"
    assert list(base_run["apps"]) == ["0"]
    assert list(noise_run["apps"]) == ["0", "1"]
    assert base_run["apps"]["0"] == noise_run["apps"]["0"]
    assert base_run["apps"]["0"]["partition"] == "victim"
    assert base_run["apps"]["0"]["collect"] is True
    aggressor = noise_run["apps"]["1"]
    assert aggressor["partition"] == "aggressor"
    assert aggressor["collect"] is False
    assert aggressor["end"] == "f"
    assert aggressor["args"] == "-msgsize 2097152 -endl -blength 1 -bpause 1"
