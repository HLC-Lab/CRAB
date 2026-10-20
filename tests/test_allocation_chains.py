"""Which chained apps (start "sN") run on another app's nodes (ADR-032).

Indices are positions in the runner's app order; "sN" names position N
(runner.py builds `dependency_map[i] = int(start[1:])`).
"""

import pytest

from crab.core.allocation.chains import resolve_chains


def test_time_started_apps_are_all_heads():
    assert resolve_chains(["0", "5.0", "0"], [None, None, None]) == {}


def test_chained_app_uses_its_predecessors_nodes():
    assert resolve_chains(["0", "s0"], [None, None]) == {1: 0}


def test_chain_is_transitive():
    assert resolve_chains(["0", "s0", "s1", "s2"], [None] * 4) == {1: 0, 2: 0, 3: 0}


def test_chain_follows_app_order_not_list_order():
    # app 0 waits for app 2, which waits for app 1 (a head)
    assert resolve_chains(["s2", "0", "s1"], [None] * 3) == {0: 1, 2: 1}


def test_same_partition_reuses_nodes():
    assert resolve_chains(["0", "s0"], ["victim", "victim"]) == {1: 0}


def test_no_partition_after_a_partitioned_app_reuses_its_nodes():
    assert resolve_chains(["0", "s0"], ["victim", ""]) == {1: 0}


def test_different_partition_makes_a_new_head():
    # "start when the victim ends, on the aggressor's nodes"
    assert resolve_chains(["0", "s0"], ["victim", "aggressor"]) == {}


def test_partition_on_a_chained_app_after_one_without_is_a_new_head():
    assert resolve_chains(["0", "s0"], [None, "victim"]) == {}


def test_partition_is_inherited_along_the_chain():
    assert resolve_chains(["0", "s0", "s1"], ["victim", None, "victim"]) == {1: 0, 2: 0}
    assert resolve_chains(["0", "s0", "s1"], ["victim", None, "aggressor"]) == {1: 0}


def test_a_partition_change_breaks_the_chain_at_that_link():
    assert resolve_chains(["0", "s0", "s1"], ["victim", "aggressor", None]) == {2: 1}


def test_two_apps_reusing_the_same_nodes_at_once_is_an_error():
    with pytest.raises(ValueError, match=r"apps 1 and 2 both start after app 0"):
        resolve_chains(["0", "s0", "s0"], [None] * 3)
    with pytest.raises(ValueError, match=r"apps 2 and 3 both start after app 1"):
        resolve_chains(["0", "s0", "s1", "s1"], [None] * 4)


def test_fan_out_onto_different_partitions_is_fine():
    assert resolve_chains(["0", "s0", "s0"], ["victim", None, "aggressor"]) == {1: 0}


def test_start_naming_no_app_is_an_error():
    with pytest.raises(ValueError, match=r"app 1: start 's5' names no app"):
        resolve_chains(["0", "s5"], [None, None])
    with pytest.raises(ValueError, match=r"app 1: start 'sx' names no app"):
        resolve_chains(["0", "sx"], [None, None])


def test_start_cycle_is_an_error():
    with pytest.raises(ValueError, match=r"cycle: app 0 -> app 1 -> app 0"):
        resolve_chains(["s1", "s0"], [None, None])
    with pytest.raises(ValueError, match=r"cycle: app 0 -> app 0"):
        resolve_chains(["s0"], [None])
