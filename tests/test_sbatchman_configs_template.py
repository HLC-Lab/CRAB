"""The committed Leonardo SbatchMan configs file has the shape SbatchMan 2.0.3 and `crab worker` need.

`examples/sbatchman/configs.leonardo.yaml` is a template a partner copies: one cluster block that gives
every job the Slurm allocation and the environment `crab worker` reads. The jobs files depend only on
the config name `crab_run_{nodes}`, so these tests pin that name, the env keys and the placeholders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "sbatchman"
TEMPLATE = EXAMPLES / "configs.leonardo.yaml"
JOBS_FILES = ["smoke.yaml", "a2a-vs-incast.yaml", "a2a-vs-a2a.yaml"]
NEEDED_ENV_KEYS = {
    "PATH",
    "CRAB_ROOT",
    "CRAB_SYSTEM",
    "CRAB_PATH_WRAPPERS",
    "CRAB_IB_DEVICES",
    "UCX_IB_SL",
    "NCCL_IB_SL",
    "SLURM_CPU_BIND",
}
ALLOWED_KEYS = {
    "account",
    "partition",
    "qos",
    "time",
    "nodes",
    "ntasks",
    "ntasks_per_node",
    "cpus_per_task",
    "gpus",
    "mem",
    "exclusive",
    "modules",
    "env",
    "custom_headers",
    "constraint",
    "nodelist",
    "exclude",
    "reservation",
    "name",
    "scheduler",
}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _env(doc: dict[str, Any]) -> dict[str, str]:
    """The default env list as a {KEY: value} dict."""
    entries = doc["leonardo"]["default_conf"]["env"]
    assert isinstance(entries, list)
    result: dict[str, str] = {}
    for entry in entries:
        key, sep, value = entry.partition("=")
        assert sep == "=", f"env entry without '=': {entry!r}"
        result[key] = value
    return result


def _expand(pattern: str, values: list[Any]) -> set[str]:
    return {pattern.replace("{nodes}", str(value)) for value in values}


def test_the_template_defines_the_leonardo_cluster_block() -> None:
    doc = _load(TEMPLATE)
    nodes = doc["variables"]["nodes"]
    assert isinstance(nodes, list) and nodes
    assert all(isinstance(n, int) for n in nodes)
    assert 2 in nodes and 4 in nodes
    block = doc["leonardo"]
    assert block["scheduler"] == "slurm"
    assert isinstance(block["default_conf"], dict)
    assert isinstance(block["configs"], list)


def test_the_config_is_named_crab_run_per_node_count() -> None:
    configs = _load(TEMPLATE)["leonardo"]["configs"]
    assert len(configs) == 1
    config = configs[0]
    assert config["name"] == "crab_run_{nodes}"
    assert config["nodes"] == "{nodes}"
    assert "env" not in config


def test_the_default_env_carries_everything_the_worker_needs() -> None:
    env = _env(_load(TEMPLATE))
    assert NEEDED_ENV_KEYS <= set(env)
    assert env["CRAB_SYSTEM"] == "leonardo"
    assert env["PATH"].startswith("<CRAB_DIR>/.venv/bin:")
    assert env["PATH"].endswith("$PATH")
    assert env["CRAB_ROOT"] == "<CRAB_DIR>"
    assert env["CRAB_PATH_WRAPPERS"] == "<CRAB_DIR>/wrappers"


def test_the_template_holds_placeholders_not_real_values() -> None:
    text = TEMPLATE.read_text()
    assert _load(TEMPLATE)["leonardo"]["default_conf"]["account"] == "<YOUR_ACCOUNT>"
    assert "<CRAB_DIR>" in text
    for forbidden in ("@", "Iscr", "/home/", "/leonardo"):
        assert forbidden not in text, forbidden


def test_the_allocation_fields_are_valid_sbatchman_slurm_keys() -> None:
    block = _load(TEMPLATE)["leonardo"]
    assert set(block["default_conf"]) <= ALLOWED_KEYS
    for config in block["configs"]:
        assert set(config) <= ALLOWED_KEYS


def test_expanding_the_nodes_variable_names_one_config_per_value() -> None:
    doc = _load(TEMPLATE)
    name = doc["leonardo"]["configs"][0]["name"]
    provided = _expand(name, doc["variables"]["nodes"])
    assert {"crab_run_2", "crab_run_4"} <= provided
    for jobs_file in JOBS_FILES:
        jobs_doc = _load(EXAMPLES / jobs_file)
        wanted: set[str] = set()
        for job in jobs_doc["jobs"]:
            wanted |= _expand(job["config"], jobs_doc["variables"]["nodes"])
        assert wanted, jobs_file
        assert wanted <= provided, (jobs_file, wanted - provided)
