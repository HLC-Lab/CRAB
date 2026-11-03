"""The `bind` resolver (ADR-043): invariants over every topology shape, then one test per rule.

The shape matrix is the cluster-agnostic guard: `test_invariants_hold_on_every_shape` runs the same
checks over layouts that none of the rules was written for. Tests that name devices build their own
shape, so a rule is never tuned to one machine.
"""

from __future__ import annotations

from typing import Any

import pytest

from crab.core.execution.bind import Bind, parse_bind
from crab.core.execution.bind_resolve import RESOLVER_VERSION, Resolved, resolve
from crab.core.execution.topology import DISTANCES, Topology
from topology_fixtures import SHAPES, make

WHERE = "app 0"

BINDS: list[dict[str, Any]] = [
    {},
    {"gpu": "per-rank"},
    {"gpu": "per-rank", "nic": "nearest"},
    {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"},
    {"gpu": "per-rank", "nic": "far"},
    {"gpu": "per-rank", "nic": "spread", "order": "spread"},
    {"gpu": "shared", "nic": "single"},
    {"gpu": "all", "cores": "nearest", "nic": "nearest"},
    {"cores": "nearest", "nic": "nearest"},
    {"cores": {"smt": True}},
    {"gpu": "per-rank", "nic": {"pick": "nearest", "ports": 2}},
]


def run(
    topology: Topology, body: dict[str, Any], ppn: int, taken: frozenset[int] = frozenset()
) -> Resolved:
    return resolve(parse_bind(body, WHERE), topology, ppn, taken, WHERE)


def devices(topology: Topology) -> set[str]:
    return {nic.dev for nic in topology.nics}


def all_cores(topology: Topology) -> set[int]:
    return {core for cores in topology.numa.values() for core in cores}


def domain_of(topology: Topology, core: int) -> int:
    return next(d for d, cores in topology.numa.items() if core in cores)


def gpu_domain(topology: Topology, gpu: int) -> int | None:
    numa = topology.gpus[gpu].numa
    return (
        numa
        if numa is not None
        else (next(iter(topology.numa)) if len(topology.numa) == 1 else None)
    )


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_invariants_hold_on_every_shape(shape: str) -> None:
    topology = SHAPES[shape]()
    successes = 0
    for body in BINDS:
        bind = parse_bind(body, WHERE)
        for ppn in (1, 2, 4):
            try:
                result = resolve(bind, topology, ppn, frozenset(), WHERE)
            except ValueError as error:
                assert str(error).startswith(f"{WHERE}: bind"), (body, ppn, str(error))
                continue
            successes += 1
            assert result == resolve(bind, topology, ppn, frozenset(), WHERE)
            check_invariants(topology, bind, ppn, result)
    assert successes >= 3


def check_invariants(topology: Topology, bind: Bind, ppn: int, result: Resolved) -> None:
    ranks = result.ranks
    assert [r.local_rank for r in ranks] == list(range(ppn))
    if bind.gpu == "per-rank":
        ids = [r.gpu for r in ranks]
        assert len(set(ids)) == ppn and all(g is not None and g < len(topology.gpus) for g in ids)
    if bind.gpu in ("none", "all"):
        assert all(r.gpu is None for r in ranks)
    assert all(set(r.nics) <= devices(topology) for r in ranks)
    assert all(r.nics == () for r in ranks) or bind.nic.pick != "default"
    used = [c for r in ranks for c in r.cores]
    assert len(used) == len(set(used)) and set(used) <= all_cores(topology)
    for rank in ranks:
        if rank.cores and rank.gpu is not None and gpu_domain(topology, rank.gpu) is not None:
            assert domain_of(topology, rank.cores[0]) == gpu_domain(topology, rank.gpu)
        if rank.nics:
            assert rank.nic_share == sum(1 for o in ranks if o.nics and o.nics[0] == rank.nics[0])
        else:
            assert rank.nic_share == 0 and rank.dist is None
        if rank.dist is not None:
            assert rank.dist in DISTANCES
        if (
            bind.nic.pick == "nearest"
            and bind.nic.ports == 1
            and rank.gpu is not None
            and rank.nics
        ):
            claimed = [n for n in topology.nics if n.near_gpu == rank.gpu]
            if claimed:
                chosen = next(n for n in topology.nics if n.dev == rank.nics[0])
                assert chosen.near_gpu == rank.gpu
                best = min(DISTANCES.index(n.dist) if n.dist else len(DISTANCES) for n in claimed)
                assert (DISTANCES.index(chosen.dist) if chosen.dist else len(DISTANCES)) == best
    assert isinstance(result.warnings, tuple)


def test_the_version_is_recorded() -> None:
    assert RESOLVER_VERSION == "1"


def test_each_gpu_gets_its_own_nic_and_a_core_next_to_both() -> None:
    result = run(
        SHAPES["four-gpus-four-nics"](),
        {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"},
        4,
    )
    assert [r.gpu for r in result.ranks] == [0, 1, 2, 3]
    assert [r.nics for r in result.ranks] == [("nic0:1",), ("nic1:1",), ("nic2:1",), ("nic3:1",)]
    assert [r.cores for r in result.ranks] == [(0,), (1,), (8,), (9,)]
    assert [(r.dist, r.nic_share) for r in result.ranks] == [("PIX", 1)] * 4
    assert result.cores_used == {0, 1, 8, 9}
    assert [w for w in result.warnings if "share" in w] == []


def test_spread_order_alternates_domains_and_keeps_rank_order_for_cores() -> None:
    result = run(
        SHAPES["four-gpus-four-nics"](),
        {"gpu": "per-rank", "order": "spread", "cores": "nearest"},
        4,
    )
    assert [r.gpu for r in result.ranks] == [0, 2, 1, 3]
    assert [r.cores for r in result.ranks] == [(0,), (8,), (1,), (9,)]


def test_spread_order_over_an_uneven_rank_count_is_a_warning() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"gpu": "per-rank", "order": "spread"}, 3)
    assert f"{WHERE}: bind.order spread is uneven: 3 ranks over 2 NUMA domains" in result.warnings


def test_per_rank_gpus_need_enough_gpus_and_the_message_has_the_numbers() -> None:
    with pytest.raises(ValueError) as error:
        run(SHAPES["eight-gpus-two-nics"](), {"gpu": "per-rank"}, 16)
    assert str(error.value) == (
        "app 0: bind.gpu per-rank needs 16 GPUs per node (ppn 16), the topology has 8; "
        'lower ppn or use bind.gpu "shared".'
    )
    with pytest.raises(
        ValueError, match='bind.gpu "per-rank" needs GPUs, but the topology lists none'
    ):
        run(SHAPES["cpu-only"](), {"gpu": "per-rank"}, 1)


def test_shared_gpus_wrap_and_all_gpus_are_not_pinned() -> None:
    assert [
        r.gpu for r in run(SHAPES["two-gpus-two-port-cards"](), {"gpu": "shared"}, 5).ranks
    ] == [0, 1, 0, 1, 0]
    assert [r.gpu for r in run(SHAPES["two-gpus-two-port-cards"](), {"gpu": "all"}, 3).ranks] == [
        None
    ] * 3


def test_a_gpu_no_nic_claims_takes_its_domains_nics_and_says_so() -> None:
    result = run(SHAPES["eight-gpus-two-nics"](), {"gpu": "per-rank", "nic": "nearest"}, 8)
    assert [r.nics[0] for r in result.ranks] == ["nic0:1"] * 4 + ["nic1:1"] * 4
    assert [r.dist for r in result.ranks] == ["PIX", None, None, None, "PIX", None, None, None]
    assert [r.nic_share for r in result.ranks] == [4] * 8
    assert f"{WHERE}: no NIC is near GPU 1; took the NICs of its NUMA domain 0" in result.warnings
    assert f"{WHERE}: 4 ranks share NIC nic0:1" in result.warnings


def test_exclusive_turns_a_shared_nic_into_an_error_with_the_numbers() -> None:
    body = {"gpu": "per-rank", "nic": {"pick": "nearest", "exclusive": True}}
    with pytest.raises(ValueError) as error:
        run(SHAPES["eight-gpus-two-nics"](), body, 8)
    assert str(error.value) == (
        "app 0: bind.nic exclusive: 4 ranks would share nic0:1 (ppn 8, the topology has 2 NIC port(s))."
    )
    assert run(SHAPES["four-gpus-four-nics"](), body, 4).ranks[0].nic_share == 1


def test_two_ports_of_a_card_and_what_stops_it() -> None:
    body = {"gpu": "per-rank", "nic": {"pick": "nearest", "ports": 2}}
    result = run(SHAPES["two-gpus-two-port-cards"](), body, 2)
    assert [r.nics for r in result.ranks] == [("rdma0:1", "rdma1:1"), ("rdma2:1", "rdma3:1")]
    with pytest.raises(
        ValueError, match=r"bind.nic ports 3: card 1a:00 has 2 port\(s\) in the topology"
    ):
        run(
            SHAPES["two-gpus-two-port-cards"](),
            {"gpu": "per-rank", "nic": {"pick": "nearest", "ports": 3}},
            2,
        )
    with pytest.raises(
        ValueError, match='bind.nic ports 2: NIC nic0:1 has no "card" in the topology'
    ):
        run(SHAPES["four-gpus-four-nics"](), body, 2)


def block_with_nics(nics: list[dict[str, Any]], gpus: int = 1) -> Topology:
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7"},
            "gpus": [{"numa": 0}] * gpus,
            "nics": nics,
        }
    )


def test_ties_go_by_distance_class_then_name_whatever_the_listing_order() -> None:
    nics = [
        {"dev": "b:1", "numa": 0, "near_gpu": 0, "dist": "PIX"},
        {"dev": "a:1", "numa": 0, "near_gpu": 0, "dist": "PIX"},
        {"dev": "0:1", "numa": 0, "near_gpu": 0, "dist": "PHB"},
    ]
    body = {"gpu": "per-rank", "nic": "nearest"}
    assert run(block_with_nics(nics), body, 1).ranks[0].nics == ("a:1",)
    assert run(block_with_nics(nics[::-1]), body, 1).ranks[0].nics == ("a:1",)


def test_single_gives_every_rank_the_nic_of_rank_zero() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"gpu": "per-rank", "nic": "single"}, 4)
    assert {r.nics for r in result.ranks} == {("nic0:1",)}
    assert result.ranks[0].nic_share == 4


def test_spread_goes_round_robin_over_all_nics() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"nic": "spread"}, 6)
    assert [r.nics[0] for r in result.ranks] == [
        "nic0:1",
        "nic1:1",
        "nic2:1",
        "nic3:1",
        "nic0:1",
        "nic1:1",
    ]


def test_far_picks_a_nic_in_another_domain_and_needs_one() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"gpu": "per-rank", "nic": "far"}, 2)
    assert [r.nics[0] for r in result.ranks] == ["nic2:1", "nic3:1"]
    with pytest.raises(
        ValueError,
        match=r"bind.nic far: no NIC outside NUMA domain 0 \(NICs are in domain\(s\): 0\)",
    ):
        run(SHAPES["amd-single-socket"](), {"gpu": "per-rank", "nic": "far"}, 1)


def test_a_nic_without_numa_on_a_single_domain_node_is_in_that_domain() -> None:
    result = run(SHAPES["amd-single-socket"](), {"gpu": "per-rank", "nic": "nearest"}, 2)
    assert [r.nics for r in result.ranks] == [("nic0:1",), ("nic0:1",)]
    assert result.ranks[0].dist == "PHB" and result.ranks[1].dist is None


def test_tags_select_nics_and_a_missing_tag_lists_what_exists() -> None:
    nics = [
        {"dev": "a:1", "numa": 0, "tags": ["leaf-A"]},
        {"dev": "b:1", "numa": 0, "tags": ["leaf-B", "x"]},
        {"dev": "c:1", "numa": 0, "tags": ["leaf-B"]},
    ]
    topology = block_with_nics(nics)
    assert [r.nics[0] for r in run(topology, {"nic": {"tag": "leaf-B"}}, 3).ranks] == [
        "b:1",
        "c:1",
        "b:1",
    ]
    with pytest.raises(
        ValueError,
        match=r"tag 'leaf-Z': no NIC has that tag \(the topology declares: leaf-A, leaf-B, x\)",
    ):
        run(topology, {"nic": {"tag": "leaf-Z"}}, 1)
    with pytest.raises(ValueError, match=r"\(the topology declares: none\)"):
        run(SHAPES["four-gpus-four-nics"](), {"nic": {"tag": "leaf-Z"}}, 1)


def two_domains_four_nics_no_gpu() -> Topology:
    """Two NUMA domains of 8 cores, no GPU, two NICs in each domain (tagged a and b)."""
    nics = [
        {"dev": f"nic{k}:1", "numa": 0 if k < 2 else 1, "tags": ["a" if k < 2 else "b"]}
        for k in range(4)
    ]
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7", "1": "8-15"},
            "gpus": [],
            "nics": nics,
        }
    )


def test_cores_follow_a_tagged_nic_when_no_gpu_is_bound() -> None:
    topology = two_domains_four_nics_no_gpu()
    result = run(topology, {"cores": "nearest", "nic": {"tag": "b"}}, 2)
    assert [r.nics[0] for r in result.ranks] == ["nic2:1", "nic3:1"]
    assert [domain_of(topology, r.cores[0]) for r in result.ranks] == [1, 1]


def test_cores_follow_a_spread_nic_when_no_gpu_is_bound() -> None:
    topology = two_domains_four_nics_no_gpu()
    result = run(topology, {"cores": "nearest", "nic": "spread"}, 3)
    assert [r.nics[0] for r in result.ranks] == ["nic0:1", "nic1:1", "nic2:1"]
    assert [r.cores for r in result.ranks] == [(0,), (1,), (8,)]


def _dev_domain(topology: Topology, dev: str) -> int | None:
    return next(nic.numa for nic in topology.nics if nic.dev == dev)


def test_a_nic_pick_defined_from_the_core_keeps_cores_first_without_a_gpu() -> None:
    topology = two_domains_four_nics_no_gpu()
    far = run(topology, {"cores": "nearest", "nic": "far"}, 2)
    assert [r.cores for r in far.ranks] == [(0,), (1,)]
    assert [_dev_domain(topology, r.nics[0]) for r in far.ranks] == [1, 1]
    single = run(topology, {"cores": "nearest", "nic": "single"}, 2)
    assert [r.cores for r in single.ranks] == [(0,), (1,)]
    assert {r.nics for r in single.ranks} == {("nic0:1",)}


def test_a_nic_domain_with_no_free_core_falls_back_with_a_warning() -> None:
    topology = two_domains_four_nics_no_gpu()
    result = run(topology, {"cores": "nearest", "nic": {"tag": "b"}}, 9)
    assert [domain_of(topology, r.cores[0]) for r in result.ranks] == [1] * 8 + [0]
    assert result.ranks[8].cores == (0,)
    assert result.ranks[8].nics == ("nic2:1",)
    assert any(
        "local rank 8 has no free core in NUMA domain 1 of its NIC nic2:1" in w
        for w in result.warnings
    )


def test_a_stack_that_cannot_select_a_nic_only_allows_the_default() -> None:
    topology = SHAPES["no-nic-selection"]()
    with pytest.raises(ValueError, match="this topology cannot select a NIC per rank"):
        run(topology, {"gpu": "per-rank", "nic": "nearest"}, 1)
    assert run(topology, {"gpu": "per-rank"}, 2).ranks[0].nics == ()


def test_the_default_nic_sets_nothing_and_launcher_cores_set_nothing() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"gpu": "per-rank"}, 2)
    assert [(r.nics, r.cores, r.dist, r.nic_share) for r in result.ranks] == [((), (), None, 0)] * 2


def test_no_nics_at_all_is_an_error_for_any_other_pick() -> None:
    topology = make({"gpu_vendor": "none", "nic_env": "UCX_NET_DEVICES", "numa": {"0": "0-3"}})
    with pytest.raises(ValueError, match='bind.nic "spread": the topology lists no NICs'):
        run(topology, {"nic": "spread"}, 1)


def test_physical_cores_come_before_smt_siblings() -> None:
    result = run(SHAPES["smt-two-sockets"](), {"gpu": "per-rank", "cores": "nearest"}, 4)
    assert [r.cores for r in result.ranks] == [(0,), (1,), (4,), (5,)]
    assert result.warnings == ()


def test_siblings_are_used_only_when_asked_and_the_error_counts_cores() -> None:
    topology = SHAPES["smt-two-sockets"]()
    with pytest.raises(ValueError) as error:
        run(topology, {"cores": "nearest"}, 9)
    assert str(error.value) == (
        "app 0: bind.cores nearest: local rank 8 has no free core in any NUMA domain; its 8 physical cores "
        "are used, 0 by other apps and 8 by earlier ranks of this app."
    )
    result = run(topology, {"cores": {"smt": True}}, 9)
    assert [r.cores[0] for r in result.ranks] == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    with pytest.raises(
        ValueError, match="its 16 cores are used, 0 by other apps and 16 by earlier ranks"
    ):
        run(topology, {"cores": {"smt": True}}, 17)


def test_a_topology_without_physical_cores_warns_that_smt_is_unknown() -> None:
    result = run(SHAPES["four-gpus-four-nics"](), {"gpu": "per-rank", "cores": "nearest"}, 2)
    assert any("no physical core list" in w for w in result.warnings)


def test_cores_nearest_needs_numa_domains() -> None:
    topology = make({"gpu_vendor": "none", "nic_env": "UCX_NET_DEVICES"})
    with pytest.raises(ValueError, match="bind.cores nearest needs topology.numa"):
        run(topology, {"cores": "nearest"}, 1)


def test_apps_in_one_group_take_cores_in_order_without_overlap() -> None:
    topology = SHAPES["four-gpus-four-nics"]()
    body = {"gpu": "per-rank", "cores": "nearest"}
    first = run(topology, body, 2)
    second = run(topology, body, 2, first.cores_used)
    assert [r.cores for r in first.ranks] == [(0,), (1,)]
    assert [r.cores for r in second.ranks] == [(2,), (3,)]
    assert first.cores_used.isdisjoint(second.cores_used)


def test_a_full_domain_is_an_error_naming_who_holds_the_cores() -> None:
    topology = SHAPES["four-gpus-four-nics"]()
    with pytest.raises(ValueError) as error:
        run(topology, {"gpu": "per-rank", "cores": "nearest"}, 1, frozenset(range(8)))
    assert str(error.value) == (
        "app 0: bind.cores nearest: local rank 0 has no free core in NUMA domain 0 (near GPU 0); its 8 "
        "physical cores are used, 8 by other apps and 0 by earlier ranks of this app."
    )


def test_ppn_below_one_is_refused_and_the_where_prefix_is_used() -> None:
    with pytest.raises(ValueError, match="experiment 'a', app 2: ppn must be at least 1, got 0"):
        resolve(Bind(), SHAPES["cpu-only"](), 0, frozenset(), "experiment 'a', app 2")
