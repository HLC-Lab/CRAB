"""`resolve(..., taken_gpus=...)`: per-rank GPUs skip the GPUs other apps hold on the node (plan 122).

Shape `four-gpus-four-nics`: GPUs 0,1 in NUMA domain 0 (cores 0-7), GPUs 2,3 in domain 1 (cores 8-15).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from crab.core.execution.bind import parse_bind
from crab.core.execution.bind_resolve import Resolved, resolve
from topology_fixtures import SHAPES

WHERE = "app 0"


def run(body: dict[str, Any], ppn: int, taken_gpus: set[int]) -> Resolved:
    topology = SHAPES["four-gpus-four-nics"]()
    return resolve(
        parse_bind(body, WHERE), topology, ppn, frozenset(), WHERE, taken_gpus=frozenset(taken_gpus)
    )


def gpus_of(resolved: Resolved) -> list[int | None]:
    return [rank.gpu for rank in resolved.ranks]


def test_per_rank_gpus_skip_the_held_ones_in_pack_order() -> None:
    assert gpus_of(run({"gpu": "per-rank"}, 2, {0, 1})) == [2, 3]


def test_per_rank_gpus_skip_the_held_ones_and_fill_the_hole() -> None:
    assert gpus_of(run({"gpu": "per-rank"}, 2, {1})) == [0, 2]


def test_per_rank_gpus_skip_the_held_ones_in_spread_order() -> None:
    body = {"gpu": "per-rank", "order": "spread"}
    assert gpus_of(run(body, 2, {0})) == [2, 1]


def test_too_few_free_gpus_is_an_error_with_the_numbers() -> None:
    expected = (
        "app 0: bind.gpu per-rank needs 2 GPUs per node (ppn 2), 1 of the topology's 4 are free "
        '(3 held by other apps on the node); lower ppn or use bind.gpu "shared"'
    )
    with pytest.raises(ValueError, match=re.escape(expected)):
        run({"gpu": "per-rank"}, 2, {0, 1, 2})


def test_the_message_without_held_gpus_is_the_old_one() -> None:
    with pytest.raises(ValueError) as caught:
        run({"gpu": "per-rank"}, 5, set())
    assert (
        'needs 5 GPUs per node (ppn 5), the topology has 4; lower ppn or use bind.gpu "shared".'
        in str(caught.value)
    )


def test_shared_and_all_ignore_the_held_gpus() -> None:
    assert gpus_of(run({"gpu": "shared"}, 2, {0, 1, 2, 3})) == [0, 1]
    assert gpus_of(run({"gpu": "all"}, 2, {0})) == [None, None]


def test_cores_and_nic_follow_the_free_gpu() -> None:
    body = {"gpu": "per-rank", "cores": "nearest", "nic": "nearest"}
    ranks = run(body, 2, {0, 1}).ranks
    assert [rank.gpu for rank in ranks] == [2, 3]
    assert [rank.nics[0] for rank in ranks] == ["nic2:1", "nic3:1"]
    assert ranks[0].cores[0] == 8
    assert ranks[1].cores[0] == 9


def test_held_indexes_the_topology_does_not_have_are_ignored() -> None:
    assert gpus_of(run({"gpu": "per-rank"}, 4, {9})) == [0, 1, 2, 3]
    with pytest.raises(
        ValueError,
        match=re.escape("3 of the topology's 4 are free (1 held by other apps on the node)"),
    ):
        run({"gpu": "per-rank"}, 4, {0, 9})
