"""The node check says which app and local rank need a NIC port it finds wrong (plan 122 S8, remark P7).

Before: the message gave only the port and its state (`mlx5_2:1 is not active (DOWN)`), because `used_by`
flattened each app's per-rank NIC entries into one set of ports. A local rank r holds entry
`nics[r % len(nics)]` (`process/manager.py`), and an entry may be several comma-joined ports.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from crab.core.execution import node_check
from crab.core.execution.node_check import Used, check_node
from crab.core.execution.node_check_run import AppDevices, run_node_check, used_by
from crab.core.execution.probe_sysfs import collect_node
from crab.core.execution.probe_topology import build_topology
from crab.core.execution.settings import LauncherSpec
from crab.core.execution.topology import Topology, parse_topology
from sysfs_fixtures import Sysfs, four_by_four
from topology_fixtures import SHAPES


def topology_of(fs: Sysfs) -> Topology:
    block, _ = build_topology(collect_node(fs.root, hostname="n"), "2026-11-01")
    return parse_topology(block, "preset 'p'")


def node_with(fs: Sysfs, dev: str, **changes: Any) -> dict[str, Any]:
    """The node of the fixture tree with the fields of one NIC port replaced."""
    node = collect_node(fs.root, hostname="n")
    for nic in node["nics"]:
        if nic["dev"] == dev:
            nic.update(changes)
    return node


def test_used_by_lists_the_local_ranks_that_need_each_port() -> None:
    app = AppDevices(nodes=("n1",), nics=("a:1", "b:1,c:1"), gpus=(), cores=(), key="0", ppn=3)
    used = used_by([app])
    assert used.needed_by == {
        "a:1": (("0", 0), ("0", 2)),
        "b:1": (("0", 1),),
        "c:1": (("0", 1),),
    }
    assert used.nics == frozenset({"a:1", "b:1", "c:1"})


def test_two_apps_that_name_the_same_port_are_both_listed() -> None:
    apps = [
        AppDevices(nodes=("n1",), nics=("a:1",), gpus=(), cores=(), key="1", ppn=1),
        AppDevices(nodes=("n1",), nics=("a:1",), gpus=(), cores=(), key="0", ppn=1),
    ]
    assert used_by(apps).needed_by["a:1"] == (("0", 0), ("1", 0))


def test_an_app_without_a_key_or_ppn_adds_no_needers() -> None:
    apps = [
        AppDevices(("n1",), ("a:1",), (), ()),
        AppDevices(("n1",), ("b:1",), (), (), key="0", ppn=0),
    ]
    used = used_by(apps)
    assert used.nics == frozenset({"a:1", "b:1"}) and used.needed_by == {}


def test_a_port_that_is_down_names_who_needs_it(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    node = node_with(fs, "mlx5_2:1", state="DOWN")
    used = Used(nics=frozenset({"mlx5_2:1"}), needed_by={"mlx5_2:1": (("0", 2),)})
    problems = check_node(node, topology_of(fs), used)
    assert "mlx5_2:1 is not active (DOWN), needed by app 0 local rank 2" in problems


def test_several_ranks_and_apps_are_worded_compactly(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    node = node_with(fs, "mlx5_2:1", state="DOWN")
    needers = (("0", 1), ("0", 3), ("1", 0))
    used = Used(nics=frozenset({"mlx5_2:1"}), needed_by={"mlx5_2:1": needers})
    assert check_node(node, topology_of(fs), used) == [
        "mlx5_2:1 is not active (DOWN), needed by app 0 local ranks 1, 3 and app 1 local rank 0"
    ]


def test_a_port_missing_from_the_node_and_a_pci_mismatch_name_the_needers_too(
    tmp_path: Path,
) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    needed = {"mlx5_9:1": (("0", 0),), "mlx5_0:1": (("1", 4),)}
    used = Used(nics=frozenset(needed), needed_by=needed)
    node = node_with(fs, "mlx5_0:1", pci="0000:99:00.0")
    problems = check_node(node, topology, used)
    want = topology.nics[0].pci
    assert problems == [
        f"mlx5_0:1 is at PCI 0000:99:00.0, the topology says {want}, needed by app 1 local rank 4",
        "mlx5_9:1 is not on this node, needed by app 0 local rank 0",
    ]


def test_without_needers_the_messages_are_unchanged(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    node = node_with(fs, "mlx5_2:1", state="DOWN")
    used = Used(nics=frozenset({"mlx5_2:1", "mlx5_9:1"}))
    assert check_node(node, topology_of(fs), used) == [
        "mlx5_2:1 is not active (DOWN)",
        "mlx5_9:1 is not on this node",
    ]


def test_the_spec_carries_the_needers_and_the_node_reads_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        line = json.dumps(
            {"host": "n1", "problems": [], "facts": {"nics": {}, "gpus": [], "numa": {}}}
        )
        return subprocess.CompletedProcess(argv, 0, line + "\n", "")

    apps = [AppDevices(("n1",), ("a:1",), (), (), key="0", ppn=1)]
    srun = LauncherSpec("srun", ("srun",), "", (), ())
    run_node_check(SHAPES["four-gpus-four-nics"](), apps, srun, str(tmp_path), fake_run)
    spec_path = tmp_path / "node_check_spec.json"
    spec = json.loads(spec_path.read_text())
    assert spec["nics"] == ["a:1"] and spec["needed_by"] == {"a:1": [["0", 0]]}

    seen: list[Used] = []

    def fake_check(node: Any, topology: Any, used: Used, *rest: Any) -> list[str]:
        seen.append(used)
        return []

    monkeypatch.setattr(
        node_check, "collect_node", lambda path: {"nics": [], "gpus": [], "numa_cpulists": {}}
    )
    monkeypatch.setattr(node_check, "check_node", fake_check)
    monkeypatch.setattr(node_check, "node_facts", lambda node: {})
    assert node_check.main([str(spec_path)]) == 0
    assert seen[0].needed_by == {"a:1": (("0", 0),)}

    del spec["needed_by"]  # an older writer
    spec_path.write_text(json.dumps(spec))
    assert node_check.main([str(spec_path)]) == 0
    assert seen[1].needed_by == {} and seen[1].nics == frozenset({"a:1"})
