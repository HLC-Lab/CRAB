"""The committed Leonardo SbatchMan configs file has the shape SbatchMan 2.0.4 and `crab worker` need.

`examples/sbatchman/configs.leonardo.yaml` is a template a partner copies: one cluster block that gives
every job the Slurm allocation and the environment `crab worker` reads. The jobs files depend only on
the config names `crab_run_{nodes}` (CPU, for Blink) and `crab_qe_{nodes}` (GPUs, for Quantum
ESPRESSO), so these tests pin those names, their modules, the env keys and the placeholders.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "sbatchman"
TEMPLATE = EXAMPLES / "configs.leonardo.yaml"
JOBS_FILES = [
    "smoke.yaml",
    "blink-noise.yaml",
    "a2a-vs-incast.yaml",
    "a2a-vs-a2a.yaml",
    "qe-alone.yaml",
    "qe-noise.yaml",
]
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
# The keyword names of SbatchMan 2.0.4's `create_slurm_config`.
ALLOWED_KEYS = {
    "name",
    "cluster_name",
    "partition",
    "nodes",
    "ntasks",
    "tasks_per_node",
    "cpus_per_task",
    "mem",
    "account",
    "time",
    "gpus",
    "constraint",
    "nodelist",
    "exclude",
    "qos",
    "reservation",
    "exclusive",
    "modules",
    "env",
    "custom_headers",
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


def _configs_by_name() -> dict[str, dict[str, Any]]:
    configs = _load(TEMPLATE)["leonardo"]["configs"]
    return {config["name"]: config for config in configs}


def test_there_are_two_configs_per_node_count_and_none_sets_env() -> None:
    configs = _load(TEMPLATE)["leonardo"]["configs"]
    assert [config["name"] for config in configs] == ["crab_run_{nodes}", "crab_qe_{nodes}"]
    for config in configs:
        assert config["nodes"] == "{nodes}"
        assert "env" not in config


def test_modules_are_per_config_because_a_shared_list_would_be_appended_to() -> None:
    """SbatchMan appends a config's list fields to `default_conf`'s: a shared list mixes MPI stacks."""
    doc = _load(TEMPLATE)
    assert "modules" not in doc["leonardo"]["default_conf"]
    by_name = _configs_by_name()
    assert by_name["crab_run_{nodes}"]["modules"] == [
        "gcc/12.2.0",
        "openmpi/4.1.6--gcc--12.2.0-cuda-12.2",
    ]
    assert by_name["crab_qe_{nodes}"]["modules"] == ["profile/chem-phys", "quantum-espresso"]


def test_only_the_qe_config_asks_for_gpus() -> None:
    by_name = _configs_by_name()
    assert by_name["crab_qe_{nodes}"]["gpus"] == 4
    assert "gpus" not in by_name["crab_run_{nodes}"]
    assert "gpus" not in _load(TEMPLATE)["leonardo"]["default_conf"]


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
    provided: set[str] = set()
    for config in doc["leonardo"]["configs"]:
        provided |= _expand(config["name"], doc["variables"]["nodes"])
    assert {"crab_run_2", "crab_run_4", "crab_qe_2", "crab_qe_4"} <= provided
    for jobs_file in JOBS_FILES:
        jobs_doc = _load(EXAMPLES / jobs_file)
        wanted: set[str] = set()
        for job in jobs_doc["jobs"]:
            wanted |= _expand(job["config"], jobs_doc["variables"]["nodes"])
        assert wanted, jobs_file
        assert wanted <= provided, (jobs_file, wanted - provided)


def test_the_template_asks_for_the_ranks_per_node_and_not_for_exclusive_nodes() -> None:
    default_conf = _load(TEMPLATE)["leonardo"]["default_conf"]
    assert default_conf["tasks_per_node"] == 4
    assert "exclusive" not in default_conf
    jobs_files = sorted(p for p in EXAMPLES.glob("*.yaml") if not p.name.startswith("configs."))
    assert jobs_files
    for path in jobs_files:
        ppns = [int(m) for m in re.findall(r'"ppn": "(\d+)"', path.read_text())]
        assert ppns, path.name
        assert max(ppns) <= default_conf["tasks_per_node"], path.name
