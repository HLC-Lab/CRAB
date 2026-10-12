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
    app_ppn,
    parse_app_env,
    parse_cpus,
    place_experiment,
    plan,
)
from crab.core.execution.placement_checks import check_experiment
from crab.core.execution.settings import from_preset
from settings_fixtures import LOCAL_DIRECT, slurm_settings

NODES = frozenset({"n0"})


@pytest.fixture(autouse=True)
def _sixteen_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare localhost counts as this machine; pin it so the tests do not depend on it."""
    monkeypatch.setattr("os.cpu_count", lambda: 16)


def _head(n: int, cpus: str | None = None, shared: bool = False, ppn: int = 4) -> Head:
    return Head(f"app {n}", NODES, parse_cpus(cpus, "t") if cpus else None, shared, ppn)


def _local(heads: list[Head], cores: int | None = 16):
    return plan(heads, "local", cores, [])


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
        _local([_head(0, ppn=8), _head(1, ppn=8), _head(2, ppn=8)], cores=16)


def test_unknown_host_cores_skip_the_capacity_check() -> None:
    placed, _ = _local([_head(0, ppn=8), _head(1, ppn=8), _head(2, ppn=8)], cores=None)
    assert placed["app 2"] == tuple(range(16, 24))


def test_a_single_head_gets_its_cpus_or_none() -> None:
    assert _local([_head(0, "4-7")]) == ({"app 0": (4, 5, 6, 7)}, [])
    assert _local([_head(0)]) == ({"app 0": None}, [])


def test_heads_on_different_nodes_do_not_compete() -> None:
    a = Head("app 0", frozenset({"n0"}), None, False, 4)
    b = Head("app 1", frozenset({"n1"}), None, False, 4)
    assert _local([a, b]) == ({"app 0": None, "app 1": None}, [])


def test_heads_are_co_located_through_a_third_head() -> None:
    a = Head("app 0", frozenset({"n0"}), None, False, 2)
    b = Head("app 1", frozenset({"n0", "n1"}), None, False, 2)
    c = Head("app 2", frozenset({"n1"}), None, False, 2)
    placed, _ = _local([a, b, c])
    assert placed == {"app 0": (0, 1), "app 1": (2, 3), "app 2": (4, 5)}


# -- plan: Slurm -----------------------------------------------------------------------------


def test_slurm_disjoint_leaves_apps_to_srun_exact_without_a_warning() -> None:
    assert plan([_head(0), _head(1)], "slurm", None, []) == (
        {"app 0": None, "app 1": None},
        [],
    )


@pytest.mark.parametrize(
    "directives",
    [[["--exclusive"]], [["exclusive"]], [{"exclusive": True}], [None, ["--exclusive"]]],
)
def test_slurm_cpus_with_exclusive_pass(directives: list) -> None:
    placed, _ = plan([_head(0, "0-3")], "slurm", None, directives)
    assert placed == {"app 0": (0, 1, 2, 3)}


@pytest.mark.parametrize(
    "directives", [[], [None, None], [["--time=00:10:00"]], [{"exclusive": False}]]
)
def test_slurm_cpus_without_exclusive_are_refused(directives: list) -> None:
    with pytest.raises(ValueError, match="--exclusive"):
        plan([_head(0, "0-3")], "slurm", None, directives)


_EXCLUSIVE = [["--exclusive"]]


def test_slurm_group_mixing_cpus_and_no_cpus_is_refused_naming_the_apps_without() -> None:
    heads = [_head(0, "0-3"), _head(1), _head(2)]
    with pytest.raises(ValueError, match=r"app 1 and app 2 share nodes .* set cpus on every app"):
        plan(heads, "slurm", None, _EXCLUSIVE)


def test_slurm_group_with_one_app_without_cpus_names_just_it() -> None:
    with pytest.raises(ValueError, match=r"^app 1 shares nodes"):
        plan([_head(0, "0-3"), _head(1)], "slurm", None, _EXCLUSIVE)


def test_slurm_group_with_cpus_on_every_app_is_accepted() -> None:
    placed, _ = plan([_head(0, "0-3"), _head(1, "4-7")], "slurm", None, _EXCLUSIVE)
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (4, 5, 6, 7)}


def test_slurm_group_with_no_cpus_is_accepted() -> None:
    assert plan([_head(0), _head(1)], "slurm", None, [])[0] == {"app 0": None, "app 1": None}


def test_slurm_mixed_cpus_on_different_nodes_are_not_one_group() -> None:
    alone = Head("app 1", frozenset({"n1"}), None, False, 4)
    placed, _ = plan([_head(0, "0-3"), alone], "slurm", None, _EXCLUSIVE)
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": None}


def test_local_group_mixing_cpus_and_no_cpus_is_split_automatically() -> None:
    placed, warnings = _local([_head(0, "0-3"), _head(1)])
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": (4, 5, 6, 7)}
    assert warnings == [AUTO_SPLIT_WARNING]


def test_slurm_shared_cores_group_may_mix_cpus_and_no_cpus() -> None:
    heads = [_head(0, "0-3", True), _head(1, shared=True)]
    placed, _ = plan(heads, "slurm", None, _EXCLUSIVE)
    assert placed == {"app 0": (0, 1, 2, 3), "app 1": None}


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


@pytest.mark.parametrize("key", ["A B", "1A", "A-B", "A=B", "", "A.B"])
def test_env_refuses_a_key_that_is_not_a_shell_name(key: str) -> None:
    with pytest.raises(ValueError, match=r"app 0: env: key .* is not a valid shell variable name"):
        parse_app_env({key: "1"}, "app 0: env")


def test_place_experiment_names_the_experiment_and_app_of_a_bad_env_key() -> None:
    with pytest.raises(ValueError, match=r"experiment e1: app 0: env: key 'A B' is not a valid"):
        place_experiment(
            "e1", ["0"], [{"env": {"A B": "1"}}], [["localhost"]], {}, {"ppn": 4}, LOCAL_DIRECT,
            ["localhost"], {},
        )  # fmt: skip


# -- place_experiment ----------------------------------------------------------------------------

_SHARED = {"mode": "shared"}
_SHARED_CORES = {"mode": "shared", "cores": "shared"}


def _place(configs: list[dict], nodes: list[list[str]], allocation: dict, settings=LOCAL_DIRECT):
    keys = [str(i) for i in range(len(configs))]
    return place_experiment(
        "e1", keys, configs, nodes, allocation, {"ppn": 4}, settings, ["localhost"], {}
    )


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
    placed = place_experiment(
        "e1", list("0123"), configs, nodes, allocation, {"ppn": 4}, settings, [], {}
    )
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


# -- check_experiment: a `cpus` the launcher ignores ------------------------------------------------

_CPUS_NOTE = (
    "experiment e1: app 0: cpus has no effect with the {} launcher; "
    "only mpirun binds to an explicit core list"
)


def _check(apps: dict, settings, launcher: str | None = None, opts: dict | None = None):
    options = {"numnodes": 1, "ppn": 1, **(opts or {})}
    return check_experiment("e1", apps, {}, options, settings, launcher)


def test_slurm_srun_with_cpus_does_not_warn_that_cpus_has_no_effect() -> None:
    opts = {"sbatch_directives": ["--exclusive"]}
    assert _check({"0": {"cpus": "0"}}, slurm_settings(), opts=opts) == []


def test_local_direct_with_cpus_warns_that_cpus_has_no_effect() -> None:
    assert _check({"0": {"cpus": "0"}}, LOCAL_DIRECT) == [_CPUS_NOTE.format("direct")]


def test_the_experiments_launcher_decides_over_the_presets() -> None:
    settings = from_preset({"scheduler": "local", "launcher": "mpirun"}, "t")
    assert _check({"0": {"cpus": "0"}}, settings) == []
    assert _check({"0": {"cpus": "0"}}, settings, launcher="direct") == [
        _CPUS_NOTE.format("direct")
    ]


def test_local_mpirun_with_cpus_does_not_warn() -> None:
    settings = from_preset({"scheduler": "local", "launcher": "mpirun"}, "t")
    assert _check({"0": {"cpus": "0"}}, settings) == []


def test_no_cpus_no_cpus_warning() -> None:
    assert _check({"0": {}}, LOCAL_DIRECT) == []
    assert _check({"0": {}}, slurm_settings()) == []


def test_the_cpus_warning_is_per_app() -> None:
    apps = {"0": {"cpus": "0"}, "1": {}, "2": {"cpus": "1"}}
    warnings = _check(apps, LOCAL_DIRECT)
    assert [w.split(":")[1] for w in warnings] == [" app 0", " app 2"]


# -- ppn per app and per experiment -----------------------------------------------------------


def test_app_ppn_is_the_apps_then_the_experiments_then_one() -> None:
    assert app_ppn({"ppn": 4}, {"ppn": 2}) == 4
    assert app_ppn({}, {"ppn": 2}) == 2
    assert app_ppn({}, {}) == 1
    assert app_ppn({"ppn": "3"}, {}) == 3


@pytest.mark.parametrize("value", ["{p}", "x", None, 0, "0", 2.5, True])
def test_app_ppn_refuses_what_is_not_a_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="ppn must be a positive integer"):
        app_ppn({"ppn": value}, {})


def test_place_experiment_names_the_app_of_a_bad_ppn() -> None:
    with pytest.raises(ValueError, match=r"experiment e1: app 0: ppn must be a positive integer"):
        place_experiment(
            "e1", ["0"], [{"ppn": "x"}], [["localhost"]], {}, {}, LOCAL_DIRECT, ["localhost"], {}
        )


def test_cpus_shorter_than_the_apps_own_ppn_is_refused() -> None:
    with pytest.raises(ValueError, match=r"app 1: cpus lists 2 core\(s\), fewer than ppn 3"):
        _local([_head(0, "0-3"), _head(1, "4-5", ppn=3)])
    assert _local([_head(0, "0-1", ppn=2)])[0] == {"app 0": (0, 1)}


def test_automatic_split_gives_each_head_a_block_of_its_own_ppn() -> None:
    placed, _ = _local([_head(0, ppn=2), _head(1, ppn=4), _head(2, ppn=1)])
    assert placed == {"app 0": (0, 1), "app 1": (2, 3, 4, 5), "app 2": (6,)}


def test_automatic_split_capacity_adds_up_each_heads_ppn() -> None:
    with pytest.raises(ValueError, match="need 10 cores"):
        _local([_head(0, ppn=2), _head(1, ppn=8)], cores=8)


def test_place_experiment_gives_each_app_its_own_ppn_over_the_experiments() -> None:
    placed = _place([{"ppn": 2}, {}], [["localhost"], ["localhost"]], _SHARED)
    assert placed.cpu_lists == [(0, 1), (2, 3, 4, 5)]


def test_a_chained_app_with_a_ppn_beyond_its_heads_cores_is_refused() -> None:
    configs = [{"cpus": "0-1", "ppn": 2}, {"start": "s0", "ppn": 4}]
    with pytest.raises(ValueError, match=r"experiment e1: app 1: ppn 4 .*app 0.* 2 core"):
        _place(configs, [["localhost"], ["localhost"]], {})
    # The heads' automatic blocks count too, not only an explicit cpus.
    configs = [{"ppn": 2}, {"ppn": 2}, {"start": "s0", "ppn": 4}]
    with pytest.raises(ValueError, match=r"experiment e1: app 2: ppn 4 .*app 0.* 2 core"):
        _place(configs, [["localhost"]] * 3, _SHARED)


def test_a_chained_app_within_its_heads_cores_is_placed_on_them() -> None:
    configs = [{"cpus": "0-3", "ppn": 4}, {"start": "s0", "ppn": 2}]
    placed = _place(configs, [["localhost"], ["localhost"]], {})
    assert placed.cpu_lists == [(0, 1, 2, 3), (0, 1, 2, 3)]


def test_check_experiment_takes_the_experiments_ppn_from_its_local_options() -> None:
    apps = {"0": {"cpus": "0-1"}}
    options = {"numnodes": 1, "ppn": 4}
    with pytest.raises(ValueError, match="experiment e1: app 0: cpus lists 2 core"):
        check_experiment("e1", apps, {}, options, LOCAL_DIRECT)
    note = [_CPUS_NOTE.format("direct")]
    assert check_experiment("e1", apps, {}, options, LOCAL_DIRECT, None, {"ppn": 2}) == note
    apps = {"0": {"cpus": "0-1", "ppn": 2}}
    assert check_experiment("e1", apps, {}, options, LOCAL_DIRECT) == note


def test_check_experiment_skips_a_ppn_token_on_an_app() -> None:
    assert check_experiment("e1", {"0": {"ppn": "{p}"}}, {}, {"numnodes": 1}, LOCAL_DIRECT) == []


# -- co_run: which apps share nodes with another app of the experiment ---------------------------


def test_two_overlapping_heads_with_disjoint_cores_are_both_disjoint() -> None:
    placed = _place([{}, {}], [["localhost"], ["localhost"]], _SHARED)
    assert placed.co_run == ["disjoint", "disjoint"]


def test_two_overlapping_heads_with_shared_cores_are_both_shared() -> None:
    placed = _place([{}, {}], [["localhost"], ["localhost"]], _SHARED_CORES)
    assert placed.co_run == ["shared", "shared"]


def test_a_lone_head_has_no_co_run() -> None:
    assert _place([{}], [["localhost"]], {}).co_run == [None]


def test_a_chained_app_takes_the_co_run_of_its_head() -> None:
    configs = [{"start": "0"}, {"start": "0"}, {"start": "s0"}, {"start": "s1"}]
    nodes = [["localhost"]] * 4
    assert _place(configs, nodes, _SHARED).co_run == ["disjoint"] * 4
    assert _place(configs, nodes, _SHARED_CORES).co_run == ["shared"] * 4


def test_a_chained_app_of_a_lone_head_has_no_co_run() -> None:
    configs = [{"start": "0"}, {"start": "s0"}]
    assert _place(configs, [["localhost"], ["localhost"]], {}).co_run == [None, None]


def test_apps_on_disjoint_nodes_have_no_co_run() -> None:
    allocation = {"partitions": {"a": _SHARED_CORES, "b": _SHARED}, "mode": "linear"}
    configs = [{"partition": "a"}, {"partition": "b"}]
    placed = place_experiment(
        "e1",
        ["0", "1"],
        configs,
        [["h0"], ["h1"]],
        allocation,
        {"ppn": 4},
        slurm_settings(),
        [],
        {},
    )
    assert placed.co_run == [None, None]


def test_co_run_follows_the_cores_setting_of_each_partition() -> None:
    allocation = {"partitions": {"a": _SHARED_CORES, "b": _SHARED}, "mode": "linear"}
    configs = [{"partition": "a"}, {"partition": "a"}, {"partition": "b"}, {"partition": "b"}]
    nodes = [["h0"], ["h0"], ["h1"], ["h1"]]
    placed = place_experiment(
        "e1", list("0123"), configs, nodes, allocation, {"ppn": 4}, slurm_settings(), [], {}
    )
    assert placed.co_run == ["shared", "shared", "disjoint", "disjoint"]
    assert placed.shared_cores == [True, True, False, False]


# -- check_experiment under srun: the core binding replaces a --cpu-bind flag ----------------------


def _srun_settings(*flags: str):
    return slurm_settings(launchers={"srun": {"flags": list(flags)}})


def _bind_note(flag: str) -> str:
    return (
        f"experiment e1: launcher flag '{flag}' is replaced by CRAB's core binding "
        "for apps that share nodes or set cpus"
    )


def _shared_check(apps: dict, settings, opts: dict | None = None):
    options = {"numnodes": 1, "ppn": 1, **(opts or {})}
    return check_experiment("e1", apps, _SHARED, options, settings)


def _bind_notes(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "core binding" in w]


def test_srun_cpu_bind_flag_with_a_shared_config_warns_once() -> None:
    warnings = _shared_check({"0": {}, "1": {}}, _srun_settings("--cpu-bind=socket"))
    assert _bind_notes(warnings) == [_bind_note("--cpu-bind=socket")]


@pytest.mark.parametrize(
    "flags", [("--cpu_bind=cores",), ("--cpu-bind", "none"), ("--cpu_bind", "none")]
)
def test_the_underscore_and_two_token_cpu_bind_forms_warn_too(flags: tuple[str, ...]) -> None:
    warnings = _shared_check({"0": {}, "1": {}}, _srun_settings(*flags))
    assert _bind_notes(warnings) == [_bind_note(flags[0])]


def test_srun_cpu_bind_flag_with_cpus_warns() -> None:
    opts = {"sbatch_directives": ["--exclusive"]}
    warnings = _check({"0": {"cpus": "0"}}, _srun_settings("--cpu-bind=socket"), opts=opts)
    assert warnings == [_bind_note("--cpu-bind=socket")]


def test_srun_cpu_bind_flag_without_co_run_or_cpus_does_not_warn() -> None:
    assert _check({"0": {}}, _srun_settings("--cpu-bind=socket")) == []


def test_srun_without_a_cpu_bind_flag_does_not_warn() -> None:
    assert _bind_notes(_shared_check({"0": {}, "1": {}}, _srun_settings("--mpi=pmix"))) == []


def test_the_experiments_launcher_flags_decide_the_bind_note() -> None:
    settings = _srun_settings("--cpu-bind=socket")
    kept = _shared_check({"0": {}, "1": {}}, settings, {"launcher_flags": ["--cpu-bind=map_cpu:0"]})
    assert _bind_notes(kept) == [_bind_note("--cpu-bind=map_cpu:0")]
    assert _bind_notes(_shared_check({"0": {}, "1": {}}, settings, {"launcher_flags": []})) == []


def test_a_cpu_bind_flag_under_mpirun_does_not_warn() -> None:
    preset = {"launcher": "mpirun", "launchers": {"srun": {"flags": ["--cpu-bind=socket"]}}}
    settings = from_preset({"scheduler": "slurm", **preset}, "t")
    assert _bind_notes(_shared_check({"0": {}, "1": {}}, settings)) == []
