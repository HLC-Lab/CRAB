"""Core lists and environments of co-located apps (execution/placement.py, ADR-033).

Before: `cpus` and `env` in an app were injected into the wrapper as plain attributes and nothing
checked them, so two apps sharing a node could silently pin the same cores or oversubscribe it.
"""

from __future__ import annotations

import pytest

from crab.core.execution.placement import (
    AUTO_SPLIT_WARNING,
    SHARED_CORES_WARNING,
    Head,
    parse_app_env,
    parse_cpus,
    place_experiment,
    plan,
)
from settings_fixtures import LOCAL_DIRECT, slurm_settings

NODES = frozenset({"n0"})


@pytest.fixture(autouse=True)
def _sixteen_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare localhost counts as this machine; pin it so the tests do not depend on it."""
    monkeypatch.setattr("os.cpu_count", lambda: 16)


def _head(n: int, cpus: str | None = None, shared: bool = False) -> Head:
    return Head(f"app {n}", NODES, parse_cpus(cpus, "t") if cpus else None, shared)


def _local(heads: list[Head], ppn: int = 4, cores: int | None = 16):
    return plan(heads, ppn, "local", cores, [])


# -- parse_cpus -----------------------------------------------------------------------------


def test_parse_cpus_ranges_and_singles_come_back_sorted() -> None:
    assert parse_cpus("0-3,8", "w") == (0, 1, 2, 3, 8)
    assert parse_cpus("5", "w") == (5,)
    assert parse_cpus("8, 0-1", "w") == (0, 1, 8)


@pytest.mark.parametrize("text", ["", "3-1", "a", "1,1", "-1", "1-3,2", "1,,2", 5, None])
def test_parse_cpus_refuses_malformed_lists(text: object) -> None:
    with pytest.raises(ValueError, match="app 3: cpus"):
        parse_cpus(text, "app 3")


def test_parse_cpus_error_names_the_value() -> None:
    with pytest.raises(ValueError, match="'3-1'"):
        parse_cpus("3-1", "app 0")


# -- plan: co-located heads on a local preset --------------------------------------------------


def test_automatic_split_gives_contiguous_blocks_and_warns_once() -> None:
    placed, warnings = _local([_head(0), _head(1)])
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (4, 5, 6, 7)}
    assert warnings == [AUTO_SPLIT_WARNING]


def test_automatic_split_skips_cores_taken_by_explicit_cpus() -> None:
    placed, _ = _local([_head(0, "0-3"), _head(1)])
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (4, 5, 6, 7)}
    placed, _ = _local([_head(0, "2-5"), _head(1)])
    assert placed["app 1"] == (0, 1, 6, 7)


def test_all_explicit_cpus_are_used_as_given_without_a_warning() -> None:
    placed, warnings = _local([_head(0, "0-3"), _head(1, "8-11")])
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (8, 9, 10, 11)}
    assert warnings == []


def test_overlapping_explicit_cpus_name_both_apps() -> None:
    with pytest.raises(ValueError, match=r"app 0 and app 1.*core\(s\) 2,3"):
        _local([_head(0, "0-3"), _head(1, "2-5")])


def test_more_cores_wanted_than_the_host_has_is_refused() -> None:
    with pytest.raises(ValueError, match="need 24 cores"):
        _local([_head(0), _head(1), _head(2)], ppn=8, cores=16)


def test_unknown_host_cores_skip_the_capacity_check() -> None:
    placed, _ = _local([_head(0), _head(1), _head(2)], ppn=8, cores=None)
    assert placed["app 2"] == tuple(range(16, 24))


def test_a_single_head_gets_its_cpus_or_none() -> None:
    assert _local([_head(0, "4-7")]) == ({"app 0": (4, 5, 6, 7)}, [])
    assert _local([_head(0)]) == ({"app 0": None}, [])


def test_heads_on_different_nodes_do_not_compete() -> None:
    a = Head("app 0", frozenset({"n0"}), None, False)
    b = Head("app 1", frozenset({"n1"}), None, False)
    assert _local([a, b]) == ({"app 0": None, "app 1": None}, [])


def test_heads_are_co_located_through_a_third_head() -> None:
    a = Head("app 0", frozenset({"n0"}), None, False)
    b = Head("app 1", frozenset({"n0", "n1"}), None, False)
    c = Head("app 2", frozenset({"n1"}), None, False)
    placed, _ = _local([a, b, c], ppn=2)
    assert placed == {"app 0": (0, 1), "app 1": (2, 3), "app 2": (4, 5)}


# -- plan: Slurm -----------------------------------------------------------------------------


def test_slurm_disjoint_leaves_apps_to_srun_exact_without_a_warning() -> None:
    assert plan([_head(0), _head(1)], 4, "slurm", None, []) == (
        {"app 0": None, "app 1": None},
        [],
    )


@pytest.mark.parametrize(
    "directives",
    [[["--exclusive"]], [["exclusive"]], [{"exclusive": True}], [None, ["--exclusive"]]],
)
def test_slurm_cpus_with_exclusive_pass(directives: list) -> None:
    placed, _ = plan([_head(0, "0-3")], 4, "slurm", None, directives)
    assert placed == {"app 0": (0, 1, 2, 3)}


@pytest.mark.parametrize(
    "directives", [[], [None, None], [["--time=00:10:00"]], [{"exclusive": False}]]
)
def test_slurm_cpus_without_exclusive_are_refused(directives: list) -> None:
    with pytest.raises(ValueError, match="--exclusive"):
        plan([_head(0, "0-3")], 4, "slurm", None, directives)


def test_cpus_shorter_than_ppn_is_refused() -> None:
    with pytest.raises(ValueError, match=r"app 0: cpus lists 2 core\(s\), fewer than ppn 4"):
        _local([_head(0, "0-1")])


# -- plan: cores shared ------------------------------------------------------------------------


def test_shared_cores_do_not_split_and_warn_once() -> None:
    placed, warnings = _local([_head(0, shared=True), _head(1, shared=True)])
    assert placed == {"app 0": None, "app 1": None}
    assert warnings == [SHARED_CORES_WARNING]


def test_shared_cores_use_explicit_cpus_even_when_they_overlap() -> None:
    placed, _ = _local([_head(0, "0-3", True), _head(1, "0-3", True)])
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (0, 1, 2, 3)}


# -- env ---------------------------------------------------------------------------------------


def test_env_is_an_object_of_strings() -> None:
    assert parse_app_env({"UCX_NET_DEVICES": "mlx5_0:1"}, "w") == {"UCX_NET_DEVICES": "mlx5_0:1"}
    assert parse_app_env({}, "w") == {}


@pytest.mark.parametrize("value", [["A=1"], {"A": 1}, {"A": None}, "A=1", None, {1: "x"}])
def test_env_refuses_anything_else(value: object) -> None:
    with pytest.raises(ValueError, match="app 0: env: must be an object of strings"):
        parse_app_env(value, "app 0: env")


def test_env_refuses_a_dropped_crab_key() -> None:
    with pytest.raises(ValueError, match="app 0: env: CRAB_MPIRUN is no longer read"):
        parse_app_env({"CRAB_MPIRUN": "mpirun"}, "app 0: env")


# -- place_experiment ----------------------------------------------------------------------------

_SHARED = {"mode": "shared"}
_SHARED_CORES = {"mode": "shared", "cores": "shared"}


def _place(configs: list[dict], nodes: list[list[str]], allocation: dict, settings=LOCAL_DIRECT):
    keys = [str(i) for i in range(len(configs))]
    return place_experiment("e1", keys, configs, nodes, allocation, 4, settings, ["localhost"], {})


def test_a_chained_successor_inherits_its_heads_list() -> None:
    configs = [{"start": "0"}, {"start": "0"}, {"start": "s0"}]
    nodes = [["localhost"]] * 3
    placed = _place(configs, nodes, _SHARED)
    assert placed.cpu_lists[2] == placed.cpu_lists[0]
    assert placed.cpu_lists[0] != placed.cpu_lists[1]
    assert placed.cpu_lists[0] is not None


def test_a_chained_app_with_its_own_cpus_is_refused() -> None:
    configs = [{}, {"start": "s0", "cpus": "0-3"}]
    with pytest.raises(ValueError, match="experiment e1: app 1: cpus cannot be set"):
        _place(configs, [["localhost"], ["localhost"]], {})


def test_place_experiment_reads_cores_shared_per_partition() -> None:
    allocation = {
        "partitions": {"a": _SHARED_CORES, "b": {"mode": "shared"}},
        "mode": "linear",
    }
    configs = [{"partition": "a"}, {"partition": "a"}, {"partition": "b"}, {"partition": "b"}]
    nodes = [["h0"], ["h0"], ["h1"], ["h1"]]
    settings = slurm_settings()
    placed = place_experiment("e1", list("0123"), configs, nodes, allocation, 4, settings, [], {})
    assert placed.cpu_lists == [None] * 4
    assert placed.warnings == [SHARED_CORES_WARNING]


def test_place_experiment_collects_each_apps_env_in_order() -> None:
    configs = [{"env": {"A": "1"}}, {}]
    placed = _place(configs, [["localhost"], ["localhost"]], {})
    assert placed.envs == [{"A": "1"}, {}]


def test_place_experiment_errors_name_the_experiment_and_app() -> None:
    with pytest.raises(ValueError, match="experiment e1: app 1: env: must be an object"):
        _place([{}, {"env": "A=1"}], [["localhost"], ["localhost"]], {})


def test_local_bare_localhost_uses_the_machines_core_count(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 6)
    with pytest.raises(ValueError, match="need 8 cores"):
        _place([{}, {}], [["localhost"], ["localhost"]], _SHARED)
