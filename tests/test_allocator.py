import random as _random

import pytest

from crab.core.allocation.allocator import NodeAllocator


class MockApp:
    def __init__(self, partition_id=None):
        self.partition_id = partition_id
        self.nodes = []

    def set_nodes(self, nodes):
        self.nodes = list(nodes)


def test_get_abs_split_even_two_apps():
    assert NodeAllocator.get_abs_split("even", 2, 8) == [4, 4]


def test_get_abs_split_even_three_apps_remainder():
    # 10 nodes, 3 apps: 3.33 each → [4, 3, 3] by largest-remainder
    assert NodeAllocator.get_abs_split("even", 3, 10) == [4, 3, 3]


def test_get_abs_split_list():
    assert NodeAllocator.get_abs_split([50, 25, 25], 3, 8) == [4, 2, 2]


def test_get_abs_split_list_unequal():
    assert NodeAllocator.get_abs_split([75, 25], 2, 8) == [6, 2]


def test_get_abs_split_e_alias():
    # 'e' still works as a backward-compat alias
    assert NodeAllocator.get_abs_split("e", 2, 8) == [4, 4]


def test_get_abs_split_none():
    assert NodeAllocator.get_abs_split(None, 2, 8) == [4, 4]


def test_get_abs_split_unknown_string_raises():
    with pytest.raises(TypeError):
        NodeAllocator.get_abs_split("50:50", 2, 8)


def test_allocate_interleaved_stride_1():
    apps = [MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(8)]
    NodeAllocator.allocate_interleaved(apps, nodes, [4, 4], stride=1)
    assert apps[0].nodes == ["n0", "n2", "n4", "n6"]
    assert apps[1].nodes == ["n1", "n3", "n5", "n7"]


def test_allocate_interleaved_stride_2():
    apps = [MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(8)]
    NodeAllocator.allocate_interleaved(apps, nodes, [4, 4], stride=2)
    assert apps[0].nodes == ["n0", "n1", "n4", "n5"]
    assert apps[1].nodes == ["n2", "n3", "n6", "n7"]


def test_allocate_interleaved_stride_default_unchanged():
    # stride=1 default must produce the same result as before
    apps = [MockApp(), MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(6)]
    NodeAllocator.allocate_interleaved(apps, nodes, [2, 2, 2])
    assert apps[0].nodes == ["n0", "n3"]
    assert apps[1].nodes == ["n1", "n4"]
    assert apps[2].nodes == ["n2", "n5"]


def test_allocate_interleaved_stride_zero_raises():
    apps = [MockApp(), MockApp()]
    with pytest.raises(ValueError, match="stride must be"):
        NodeAllocator.allocate_interleaved(apps, ["n0", "n1"], [1, 1], stride=0)


def test_allocate_random_deterministic_with_seed():
    apps = [MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(8)]
    NodeAllocator.allocate_random(apps, nodes, [4, 4], seed=42)
    expected = list(nodes)
    _random.Random(42).shuffle(expected)
    assert apps[0].nodes == expected[:4]
    assert apps[1].nodes == expected[4:]


def test_allocate_random_does_not_mutate_node_list():
    apps = [MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(8)]
    original = list(nodes)
    NodeAllocator.allocate_random(apps, nodes, [4, 4], seed=42)
    assert nodes == original


def test_allocate_random_covers_all_nodes():
    apps = [MockApp(), MockApp(), MockApp()]
    nodes = [f"n{i}" for i in range(9)]
    NodeAllocator.allocate_random(apps, nodes, [3, 3, 3], seed=7)
    all_assigned = apps[0].nodes + apps[1].nodes + apps[2].nodes
    assert sorted(all_assigned) == sorted(nodes)


def test_partitioned_linear_layout_equal_share():
    victim = MockApp("victim")
    aggressor = MockApp("aggressor")
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {"victim": {}, "aggressor": {}},
    }
    NodeAllocator.allocate_partitioned([victim, aggressor], nodes, allocation)
    assert victim.nodes == ["n0", "n1", "n2", "n3"]
    assert aggressor.nodes == ["n4", "n5", "n6", "n7"]


def test_partitioned_linear_layout_explicit_share():
    victim = MockApp("victim")
    aggressor = MockApp("aggressor")
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {"victim": {"share": 75}, "aggressor": {"share": 25}},
    }
    NodeAllocator.allocate_partitioned([victim, aggressor], nodes, allocation)
    assert len(victim.nodes) == 6
    assert len(aggressor.nodes) == 2


def test_partitioned_interleaved_layout():
    victim = MockApp("victim")
    aggressor = MockApp("aggressor")
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "interleaved",
        "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}},
    }
    NodeAllocator.allocate_partitioned([victim, aggressor], nodes, allocation)
    assert victim.nodes == ["n0", "n2", "n4", "n6"]
    assert aggressor.nodes == ["n1", "n3", "n5", "n7"]


def test_partitioned_per_partition_subsplit():
    v1 = MockApp("victim")
    v2 = MockApp("victim")
    ag = MockApp("aggressor")
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {
            "victim": {"share": 50, "mode": "linear", "split": [50, 50]},
            "aggressor": {"share": 50},
        },
    }
    NodeAllocator.allocate_partitioned([v1, v2, ag], nodes, allocation)
    assert v1.nodes == ["n0", "n1"]
    assert v2.nodes == ["n2", "n3"]
    assert ag.nodes == ["n4", "n5", "n6", "n7"]


def test_partitioned_mixed_share_raises():
    apps = [MockApp("a"), MockApp("b")]
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {"a": {"share": 50}, "b": {}},
    }
    with pytest.raises(ValueError, match="Either all partitions"):
        NodeAllocator.allocate_partitioned(apps, nodes, allocation)


# ---------------------------------------------------------------------------
# Critical: allocate_partitioned accepts shares summing to >100
# ---------------------------------------------------------------------------


def test_partitioned_shares_over_100_raises():
    """Two 60-share partitions sum to 120 — must raise, not silently inflate counts."""
    apps = [MockApp("a"), MockApp("b")]
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {"a": {"share": 60}, "b": {"share": 60}},
    }
    with pytest.raises(ValueError):
        NodeAllocator.allocate_partitioned(apps, nodes, allocation)


def test_partitioned_shares_exactly_100_accepted():
    """Shares summing to exactly 100 must not raise."""
    apps = [MockApp("a"), MockApp("b")]
    nodes = [f"n{i}" for i in range(8)]
    allocation = {
        "mode": "linear",
        "partitions": {"a": {"share": 60}, "b": {"share": 40}},
    }
    NodeAllocator.allocate_partitioned(apps, nodes, allocation)
    assert len(apps[0].nodes) + len(apps[1].nodes) == 8
