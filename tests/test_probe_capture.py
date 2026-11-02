"""A real node captured with `crab probe --json`, held to what the vendor tool printed in the same job.

Nothing here knows which cluster the node came from. The checks are the ones that must hold on any
node: the probe's distance classes equal the vendor's, and the resolver puts each rank's NIC where
the vendor matrix says it is nearest.
"""

from __future__ import annotations

from typing import Any

import pytest

from crab.core.execution.bind import parse_bind
from crab.core.execution.bind_resolve import resolve
from crab.core.execution.node_check import compare_nodes, node_facts
from crab.core.execution.probe_topology import build_topology, distance
from crab.core.execution.topology import DISTANCES
from topology_fixtures import captured_doc, captured_four_gpu_four_nic_infiniband

NAME = "four_gpu_four_nic_infiniband"


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    return captured_doc(NAME)


def rank_of(vendor: str) -> int:
    return DISTANCES.index(vendor) if vendor in DISTANCES else len(DISTANCES)


def test_the_capture_holds_no_hostname(doc: dict[str, Any]) -> None:
    assert doc["node"]["hostname"] == "captured-node"


def test_every_gpu_nic_pair_has_the_distance_class_the_vendor_tool_printed(
    doc: dict[str, Any],
) -> None:
    node, vendor = doc["node"], doc["vendor_distances"]
    for g, gpu in enumerate(node["gpus"]):
        for n, nic in enumerate(node["nics"]):
            found = distance(nic["pci_path"], gpu["pci_path"], nic["numa"], gpu["numa"])
            assert found == vendor[g][n], (g, n)


def test_the_block_names_each_nics_nearest_gpu_as_the_vendor_matrix_does(
    doc: dict[str, Any],
) -> None:
    block, warnings = build_topology(doc["node"], "2026-11-01")
    assert warnings == []
    vendor = doc["vendor_distances"]
    for n, nic in enumerate(block["nics"]):
        column = [vendor[g][n] for g in range(len(vendor))]
        assert vendor[nic["near_gpu"]][n] == nic["dist"] == min(column, key=rank_of)


def test_cores_and_link_facts_agree_with_the_node(doc: dict[str, Any]) -> None:
    block, _ = build_topology(doc["node"], "2026-11-01")
    assert block["gpu_vendor"] == "nvidia" and block["nic_env"] == "UCX_NET_DEVICES"
    assert all(nic["link_layer"] == "InfiniBand" and nic["rate_mbps"] > 0 for nic in block["nics"])
    assert set(block["physical"]) == set(block["numa"])


def test_the_resolver_puts_each_rank_on_the_nic_the_vendor_matrix_says_is_nearest(
    doc: dict[str, Any],
) -> None:
    topology = captured_four_gpu_four_nic_infiniband()
    vendor = doc["vendor_distances"]
    devs = [nic.dev for nic in topology.nics]
    bind = parse_bind({"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}, "app 0")
    result = resolve(bind, topology, len(topology.gpus), frozenset(), "app 0")
    for rank in result.ranks:
        assert rank.gpu is not None
        row = vendor[rank.gpu]
        assert row[devs.index(rank.nics[0])] == min(row, key=rank_of)
        domain = topology.gpus[rank.gpu].numa
        assert domain is not None and rank.cores[0] in topology.numa[domain]
    assert len({r.nics[0] for r in result.ranks}) == len(result.ranks)
    assert all(r.nic_share == 1 for r in result.ranks)


def test_asking_for_more_gpus_than_the_node_has_names_both_numbers() -> None:
    topology = captured_four_gpu_four_nic_infiniband()
    bind = parse_bind({"gpu": "per-rank"}, "app 0")
    with pytest.raises(ValueError, match=r"needs 5 GPUs per node \(ppn 5\), the topology has 4"):
        resolve(bind, topology, 5, frozenset(), "app 0")


def test_two_real_nodes_of_one_type_compare_as_alike_and_a_moved_nic_is_named(
    doc: dict[str, Any],
) -> None:
    second = captured_doc("four_gpu_four_nic_infiniband_second_node")["facts"]
    first = node_facts(doc["node"])
    assert compare_nodes({"node-a": first, "node-b": second}) == []
    moved = {**second, "nics": {**second["nics"], "mlx5_2:1": "0000:99:00.0"}}
    assert compare_nodes({"node-a": first, "node-b": moved}) == [
        "active NIC ports differ from node-a: mlx5_2:1 is in another PCI slot on node-b"
    ]
