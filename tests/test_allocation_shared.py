"""The allocator's `shared` mode (ADR-033): every chain head of a scope gets all its nodes."""

import pytest

from crab.core.allocation.allocator import NodeAllocator

NODES = ["n0", "n1", "n2", "n3"]


class StubApp:
    def __init__(self, partition_id=None, start="0"):
        self.partition_id = partition_id
        self.start_string = start
        self.nodes: list[str] = []

    def set_nodes(self, nodes):
        self.nodes = list(nodes)


def test_top_level_shared_gives_every_head_all_nodes():
    apps = [StubApp(), StubApp(), StubApp()]
    NodeAllocator.allocate_experiment(apps, NODES, {"mode": "shared"})
    assert [a.nodes for a in apps] == [NODES, NODES, NODES]


def test_shared_keeps_the_node_list_order():
    apps = [StubApp(), StubApp()]
    nodes = ["n3", "n1", "n2"]
    NodeAllocator.allocate_experiment(apps, nodes, {"mode": "shared"})
    assert apps[0].nodes == nodes and apps[1].nodes == nodes


def test_shared_apps_do_not_alias_one_list():
    apps = [StubApp(), StubApp()]
    NodeAllocator.allocate_experiment(apps, NODES, {"mode": "shared"})
    apps[0].nodes.append("x")
    assert apps[1].nodes == NODES


def test_chained_app_gets_its_heads_nodes_under_shared():
    apps = [StubApp(), StubApp(start="s0"), StubApp()]
    NodeAllocator.allocate_experiment(apps, NODES, {"mode": "shared"})
    assert apps[1].nodes == apps[0].nodes == NODES
    assert apps[2].nodes == NODES


def test_shared_partition_gives_its_heads_the_partition_nodes():
    alloc = {"partitions": {"victim": {"share": 50, "mode": "shared"}, "aggressor": {"share": 50}}}
    apps = [
        StubApp("victim"),
        StubApp("victim"),
        StubApp("aggressor"),
        StubApp("aggressor"),
    ]
    NodeAllocator.allocate_experiment(apps, NODES, alloc)
    assert apps[0].nodes == ["n0", "n1"]
    assert apps[1].nodes == ["n0", "n1"]
    assert apps[2].nodes == ["n2"]
    assert apps[3].nodes == ["n3"]


def test_chained_app_in_a_shared_partition_keeps_adr_032():
    alloc = {"partitions": {"victim": {"share": 50, "mode": "shared"}, "aggressor": {"share": 50}}}
    apps = [
        StubApp("victim"),
        StubApp("victim", start="s0"),
        StubApp("victim"),
        StubApp("aggressor"),
    ]
    NodeAllocator.allocate_experiment(apps, NODES, alloc)
    assert apps[0].nodes == apps[1].nodes == apps[2].nodes == ["n0", "n1"]
    assert apps[3].nodes == ["n2", "n3"]


@pytest.mark.parametrize("cores", [None, "shared", "disjoint"])
def test_cores_does_not_change_the_nodes(cores):
    alloc = {"mode": "shared"} if cores is None else {"mode": "shared", "cores": cores}
    apps = [StubApp(), StubApp()]
    NodeAllocator.allocate_experiment(apps, NODES, alloc)
    assert [a.nodes for a in apps] == [NODES, NODES]


def test_top_level_shared_with_partitions_raises():
    alloc = {"mode": "shared", "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}}}
    apps = [StubApp("victim"), StubApp("aggressor")]
    with pytest.raises(ValueError, match="shared"):
        NodeAllocator.allocate_experiment(apps, NODES, alloc)
