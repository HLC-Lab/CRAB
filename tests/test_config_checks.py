"""Config values the engine used to ignore or misread are errors before anything runs.

Before: `"convergeall": "false"` was truthy (runner.py `bool(...)`), an unknown `outformat`
wrote no data (data/utils.py only handled csv/hdf), a typo in `allocation.mode` fell back to
linear (runner.py/allocator.py `else` branches), and an app without `path` was skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab import paths
from crab.core.config_checks import parse_bool
from crab.core.engine import Engine

REPO = Path(__file__).resolve().parents[1]


def _run(global_options: dict | None = None, local: dict | None = None, apps: dict | None = None):
    exp: dict = {"apps": apps if apps is not None else {"0": {"path": "a.py"}}}
    if local is not None:
        exp["local_options"] = local
    config = {"global_options": global_options or {}, "experiments": {"e1": exp}}
    Engine(logger=MagicMock()).run(config=config, environment={}, is_worker=False)


def _passes_checks(**kwargs) -> None:
    # No numnodes: reaching the engine's own "numnodes is required" means the checks passed.
    with pytest.raises(ValueError, match="numnodes is required"):
        _run(**kwargs)


@pytest.mark.parametrize("key", ["convergeall", "retain_files"])
@pytest.mark.parametrize("value", ["yes", 1, "", None])
def test_option_booleans_must_be_true_or_false(key: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"e1.*{key}"):
        _run(global_options={key: value})


@pytest.mark.parametrize("value", [True, False, "true", "False"])
def test_option_booleans_accept_real_and_spelled_booleans(value: object) -> None:
    _passes_checks(global_options={"convergeall": value}, local={"retain_files": value})


def test_spelled_false_means_false() -> None:
    assert parse_bool("false", "convergeall") is False
    assert parse_bool("TRUE", "convergeall") is True


def test_app_collect_must_be_a_boolean() -> None:
    with pytest.raises(ValueError, match=r"e1.*app 0.*collect"):
        _run(apps={"0": {"path": "a.py", "collect": "maybe"}})


@pytest.mark.parametrize("fmt", ["hdf", "json", "CSV "])
def test_only_csv_output_is_accepted(fmt: str) -> None:
    with pytest.raises(ValueError, match=r"outformat.*csv"):
        _run(local={"outformat": fmt})


def test_unknown_allocation_mode_is_refused() -> None:
    with pytest.raises(
        ValueError, match=r"allocation\.mode 'interleave'.*linear, interleaved, random"
    ):
        _run(global_options={"allocation": {"mode": "interleave"}})


def test_unknown_partition_mode_is_refused() -> None:
    alloc = {"partitions": {"left": {"share": 50, "mode": "rnd"}, "right": {"share": 50}}}
    with pytest.raises(ValueError, match=r"partition 'left'.*'rnd'"):
        _run(global_options={"allocation": alloc})


@pytest.mark.parametrize("app", [{"args": "-x"}, {"path": ""}, {"path": "  "}])
def test_app_without_path_is_refused(app: dict) -> None:
    with pytest.raises(ValueError, match=r"e1.*app 0.*path"):
        _run(apps={"0": app})


def test_legacy_applications_form_is_checked_too() -> None:
    config = {"global_options": {}, "applications": {"0": {"args": "-x"}}}
    with pytest.raises(ValueError, match=r"app 0.*path"):
        Engine(logger=MagicMock()).run(config=config, environment={}, is_worker=False)


# Examples known to fail at launch; none today. Add an entry with its cause, and remove it when
# the cause is fixed (a strict xfail flags a stale entry).
_KNOWN_BROKEN: dict[str, str] = {}


def _example_params():
    for f in sorted((REPO / "examples").rglob("*.json")):
        rel = str(f.relative_to(REPO))
        marks = []
        if rel in _KNOWN_BROKEN:
            marks.append(pytest.mark.xfail(reason=_KNOWN_BROKEN[rel], strict=True))
        yield pytest.param(f, id=rel, marks=marks)


@pytest.mark.parametrize("path", list(_example_params()))
def test_every_shipped_example_passes_the_checks(path: Path) -> None:
    from crab.core.config_checks import check_config

    check_config(json.loads(path.read_text()))


def _local_preset_settings():
    from crab.core.execution.settings import from_preset

    presets = json.loads(paths.shipped_presets().read_text())
    body = dict(presets["local"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "local")


@pytest.mark.parametrize(
    "path",
    sorted((REPO / "examples" / "local").glob("*.json")),
    ids=lambda p: p.name,
)
def test_local_examples_fit_the_local_preset(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from crab.core.config_checks import check_config

    monkeypatch.setattr("os.cpu_count", lambda: 8)
    check_config(json.loads(path.read_text()), _local_preset_settings())


# --- Allocations --------------------------------------------------------------------------
# Before: a split over 100 failed only at setup (allocator.py get_abs_split), one under 100
# left nodes idle, a split of the wrong length was padded/truncated, a zero-node app aborted
# the experiment at launch (process/manager.py run_job), and an unknown partition name gave
# its app no nodes (allocator.py allocate_partitioned matches apps by name).


def _alloc_config(
    allocation: dict | None,
    apps: list[dict] | int,
    numnodes: object = "8",
    local: dict | None = None,
) -> dict:
    app_list = [{"path": "a.py"} for _ in range(apps)] if isinstance(apps, int) else apps
    g: dict = {"numnodes": numnodes}
    if allocation is not None:
        g["allocation"] = allocation
    exp: dict = {"apps": {str(i): a for i, a in enumerate(app_list)}}
    if local is not None:
        exp["local_options"] = local
    return {"global_options": g, "experiments": {"e1": exp}}


@pytest.mark.parametrize("split", [[60, 50], [70, 50], ["50", "60"]])
def test_split_over_100_is_refused(split: list) -> None:
    from crab.core.config_checks import check_config

    with pytest.raises(ValueError, match=r"experiment e1: allocation\.split sums to \d+.*100"):
        check_config(_alloc_config({"mode": "linear", "split": split}, 2))


def test_partition_split_over_100_is_refused() -> None:
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"grp": {"split": [60, 60]}}}
    apps = [{"path": "a.py", "partition": "grp"}, {"path": "a.py", "partition": "grp"}]
    with pytest.raises(ValueError, match=r"e1: allocation\.partitions\.grp\.split sums to 120"):
        check_config(_alloc_config(alloc, apps))


def test_split_under_100_is_a_warning_not_an_error() -> None:
    """A solo baseline on half the nodes is legitimate (co_scheduling.json, experiment 02)."""
    from crab.core.config_checks import check_config

    warnings = check_config(_alloc_config({"mode": "linear", "split": [50]}, 1))
    assert warnings == ["experiment e1: allocation.split sums to 50; 50% of the nodes stay idle"]


def test_partition_split_under_100_is_a_warning() -> None:
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"grp": {"split": [40, 40]}}}
    apps = [{"path": "a.py", "partition": "grp"}, {"path": "a.py", "partition": "grp"}]
    assert check_config(_alloc_config(alloc, apps)) == [
        "experiment e1: allocation.partitions.grp.split sums to 80; 20% of the nodes stay idle"
    ]


def test_engine_logs_config_warnings_and_goes_on() -> None:
    logger = MagicMock()
    config = _alloc_config({"mode": "linear", "split": [50]}, 1, numnodes=None)
    with pytest.raises(ValueError, match="numnodes is required"):
        Engine(logger=logger).run(config=config, environment={}, is_worker=False)
    logger.warning.assert_any_call(
        "experiment e1: allocation.split sums to 50; 50% of the nodes stay idle"
    )


@pytest.mark.parametrize("split", [[100], [50, 25, 25]])
def test_split_length_must_match_the_apps(split: list) -> None:
    from crab.core.config_checks import check_config

    with pytest.raises(
        ValueError, match=rf"e1: allocation\.split has {len(split)} entries for 2 apps"
    ):
        check_config(_alloc_config({"mode": "linear", "split": split}, 2))


def test_local_split_length_is_checked_against_that_experiment() -> None:
    from crab.core.config_checks import check_config

    local = {"allocation": {"mode": "linear", "split": [50, 50]}}
    with pytest.raises(ValueError, match=r"e1: allocation\.split has 2 entries for 1 apps"):
        check_config(_alloc_config({"mode": "linear"}, 1, local=local))


@pytest.mark.parametrize(
    ("allocation", "apps", "numnodes", "app"),
    [
        ({"mode": "linear"}, 3, "2", "2"),
        ({"mode": "interleaved", "split": [95, 5]}, 2, 8, "1"),
        (
            {"partitions": {"v": {"share": 50}, "a": {"share": 50}}},
            [{"path": "a.py", "partition": "v"}] * 3,
            "4",
            "2",
        ),
        ({"partitions": {"v": {}, "a": {}}}, [{"path": "a.py"}], "4", "0"),
    ],
)
def test_an_app_with_zero_nodes_is_refused(
    allocation: dict, apps: object, numnodes: object, app: str
) -> None:
    from crab.core.config_checks import check_config

    with pytest.raises(ValueError, match=rf"experiment e1: app {app} would get 0 of"):
        check_config(_alloc_config(allocation, apps, numnodes=numnodes))


def test_chained_apps_reuse_their_heads_node() -> None:
    """ADR-032: a chain needs only its head's nodes."""
    from crab.core.config_checks import check_config

    apps = [{"path": "a.py", "start": "0"}, {"path": "a.py", "start": "s0"}]
    assert check_config(_alloc_config({"mode": "linear"}, apps, numnodes="1")) == []


def test_unknown_partition_name_is_refused() -> None:
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"victim": {}, "aggressor": {}}}
    apps = [{"path": "a.py", "partition": "victim"}, {"path": "a.py", "partition": "agressor"}]
    with pytest.raises(ValueError, match=r"e1: app 1: partition 'agressor'.*victim, aggressor"):
        check_config(_alloc_config(alloc, apps))


def test_partition_without_partitioned_allocation_is_refused() -> None:
    """A local allocation replaces the global one, partitions included (shallow merge)."""
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"victim": {}, "aggressor": {}}}
    apps = [{"path": "a.py", "partition": "victim"}]
    with pytest.raises(ValueError, match=r"e1: app 0: partition 'victim'.*defines no partitions"):
        check_config(_alloc_config(alloc, apps, local={"allocation": {"mode": "linear"}}))


@pytest.mark.parametrize(
    ("allocation", "apps", "numnodes", "local"),
    [
        # No allocation, or the dashboard's bare linear override.
        (None, 2, "8", None),
        ({"mode": "linear"}, 1, "8", {"allocation": {"mode": "linear"}}),
        # By-app splits, numbers and numeric strings, within the allocator's tolerance.
        ({"mode": "interleaved", "stride": 2, "split": [60, 40]}, 2, "10", None),
        ({"mode": "random", "seed": 7, "split": ["33.3", "33.3", "33.4"]}, 3, 3, None),
        ({"mode": "linear", "split": "even"}, 2, "2", None),
        # {var} tokens are substituted later by SbatchMan: no numeric checks on them.
        ({"mode": "linear", "split": ["{left}", 50]}, 2, "8", None),
        ({"mode": "linear", "split": "{split}"}, 2, "8", None),
        ({"mode": "linear"}, 4, "{nodes}", None),
        # Named groups as the dashboard emits them (split normalized to partitions).
        (
            {"mode": "linear", "partitions": {"group_1": {}, "group_2": {}}},
            [{"path": "a.py", "partition": "group_1"}, {"path": "a.py", "partition": "group_2"}],
            "8",
            None,
        ),
        (
            {"mode": "interleaved", "partitions": {"v": {"share": 35}, "a": {"share": "{s}"}}},
            [{"path": "a.py", "partition": "v"}, {"path": "a.py", "partition": "a"}],
            "16",
            None,
        ),
        (
            {"partitions": {"grp": {"share": 100, "mode": "interleaved", "split": [50, 50]}}},
            [{"path": "a.py", "partition": "grp"}, {"path": "a.py", "partition": "grp"}],
            "8",
            None,
        ),
        # A partition with no apps leaves its nodes idle on purpose (a solo baseline).
        (
            {"partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}}},
            [{"path": "a.py", "partition": "victim"}],
            "8",
            None,
        ),
    ],
)
def test_legitimate_allocations_pass(
    allocation: dict | None, apps: object, numnodes: object, local: dict | None
) -> None:
    from crab.core.config_checks import check_config

    assert check_config(_alloc_config(allocation, apps, numnodes=numnodes, local=local)) == []


def test_split_counts_only_apps_that_get_a_share() -> None:
    """ADR-032: an app that reuses its predecessor's nodes takes no share of the split."""
    from crab.core.config_checks import check_config

    apps = [{"path": "a.py"}, {"path": "a.py", "start": "s0"}, {"path": "a.py"}]
    assert check_config(_alloc_config({"split": [75, 25]}, apps, numnodes="4")) == []


def test_split_listing_a_chained_app_names_it() -> None:
    from crab.core.config_checks import check_config

    apps = [{"path": "a.py"}, {"path": "a.py", "start": "s0"}]
    with pytest.raises(ValueError, match=r"2 entries for 1 apps.*app 1 runs on another app's"):
        check_config(_alloc_config({"split": [50, 50]}, apps, numnodes="4"))


def test_partition_split_counts_only_members_that_get_a_share() -> None:
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"victim": {"split": [60, 40]}, "aggressor": {}}}
    apps = [
        {"path": "a.py", "partition": "victim"},
        {"path": "a.py", "partition": "victim", "start": "s0"},
        {"path": "a.py", "partition": "victim"},
        {"path": "a.py", "partition": "aggressor"},
    ]
    assert check_config(_alloc_config(alloc, apps, numnodes="10")) == []


@pytest.mark.parametrize(
    ("starts", "message"),
    [
        (["0", "s0", "s0"], r"experiment e1: apps 1 and 2 both start after app 0"),
        (["0", "s7"], r"experiment e1: app 1: start 's7' names no app"),
        (["s1", "s0"], r"experiment e1: start values form a cycle"),
    ],
)
def test_chain_errors_name_the_experiment(starts: list[str], message: str) -> None:
    from crab.core.config_checks import check_config

    apps = [{"path": "a.py", "start": s} for s in starts]
    with pytest.raises(ValueError, match=message):
        check_config(_alloc_config({"mode": "linear"}, apps, numnodes="4"))


def test_zero_node_message_no_longer_blames_chains() -> None:
    from crab.core.config_checks import check_config

    alloc = {"partitions": {"victim": {}, "aggressor": {}}}
    apps = [{"path": "a.py"}]
    with pytest.raises(ValueError, match=r"app 0 would get 0 of 4 nodes") as info:
        check_config(_alloc_config(alloc, apps, numnodes="4"))
    assert "does not reuse" not in str(info.value)


# --- Launcher options, managed Slurm directives and preset fit ---------------------------
# Before: an invalid `launcher` failed at setup inside the job, a `--nodes` in sbatch_directives
# was dropped with only a log warning (slurm.py generate_header), and a config the preset could
# not run (srun on a local preset, direct with 2 nodes) failed after submit.


def _cfg(global_options: dict | None = None, local: dict | None = None) -> dict:
    exp: dict = {"apps": {"0": {"path": "a.py"}}}
    if local is not None:
        exp["local_options"] = local
    return {"global_options": global_options or {}, "experiments": {"e1": exp}}


def _check(config: dict, settings: object = None) -> list[str]:
    from crab.core.config_checks import check_config

    return check_config(config, settings)  # type: ignore[arg-type]


def test_invalid_launcher_is_refused_globally_and_per_experiment() -> None:
    message = r"experiment e1: launcher 'pbs' must be one of srun, mpirun, direct"
    with pytest.raises(ValueError, match=message):
        _check(_cfg({"launcher": "pbs"}))
    with pytest.raises(ValueError, match=message):
        _check(_cfg(local={"launcher": "pbs"}))


@pytest.mark.parametrize("flags", ["--x", [1], ["-a", None]])
def test_launcher_flags_must_be_a_list_of_strings(flags: object) -> None:
    with pytest.raises(
        ValueError, match=r"experiment e1: launcher_flags must be a list of strings"
    ):
        _check(_cfg({"launcher_flags": flags}))


@pytest.mark.parametrize("launcher", ["srun", "mpirun", "direct", "{launcher}"])
def test_valid_launcher_options_pass(launcher: str) -> None:
    assert _check(_cfg({"launcher": launcher, "launcher_flags": ["--bind-to", "core"]})) == []


@pytest.mark.parametrize(
    ("directive", "key", "field"),
    [
        ("--nodes=2", "nodes", "numnodes"),
        ("-N 2", "N", "numnodes"),
        ("--ntasks=8", "ntasks", "numnodes and ppn"),
        ("-n 8", "n", "numnodes and ppn"),
        ("--ntasks-per-node=4", "ntasks-per-node", "ppn"),
    ],
)
@pytest.mark.parametrize(
    ("option", "source"),
    [("sbatch_directives", "sbatch_directives"), ("system_sbatch", "the preset's sbatch")],
)
def test_managed_directive_is_refused_with_the_crab_field(
    option: str, source: str, directive: str, key: str, field: str
) -> None:
    with pytest.raises(ValueError, match=rf"{source}.*'{key}'.*manages.*{field}"):
        _check(_cfg({option: [directive]}))


def test_managed_directive_in_dict_form_is_refused() -> None:
    with pytest.raises(ValueError, match=r"sbatch_directives.*'nodes'.*numnodes"):
        _check(_cfg({"sbatch_directives": {"nodes": 2}}))


@pytest.mark.parametrize("option", ["sbatch_directives", "system_sbatch"])
def test_unmanaged_directives_pass(option: str) -> None:
    assert _check(_cfg({option: ["--time=00:10:00", "--exclusive", "-J name", ""]})) == []
    assert _check(_cfg({option: {"time": "00:10:00", "exclusive": True}})) == []


def test_srun_needs_a_slurm_preset() -> None:
    from settings_fixtures import LOCAL_DIRECT

    with pytest.raises(ValueError, match=r"experiment e1: the srun launcher needs a Slurm preset"):
        _check(_cfg(local={"launcher": "srun"}), LOCAL_DIRECT)


@pytest.mark.parametrize("field", ["numnodes", "ppn"])
@pytest.mark.parametrize("preset", ["local", "slurm"])
def test_direct_launcher_runs_one_process(field: str, preset: str) -> None:
    from settings_fixtures import LOCAL_DIRECT, slurm_settings

    settings = LOCAL_DIRECT if preset == "local" else slurm_settings(launcher="direct")
    config = _cfg({"numnodes": 1, "ppn": 1, field: 2})
    with pytest.raises(ValueError, match=r"direct launcher runs a single process.*ppn 1.*mpirun"):
        _check(config, settings)


def test_direct_launcher_from_the_options_runs_one_process() -> None:
    from settings_fixtures import slurm_settings

    with pytest.raises(ValueError, match=r"direct launcher runs a single process"):
        _check(_cfg({"numnodes": 2, "launcher": "direct"}), slurm_settings())


def test_direct_launcher_with_one_node_and_one_task_passes() -> None:
    from settings_fixtures import LOCAL_DIRECT

    assert _check(_cfg({"numnodes": "1", "ppn": 1}), LOCAL_DIRECT) == []


def test_numnodes_above_the_local_hosts_is_refused() -> None:
    from settings_fixtures import LOCAL_DIRECT

    with pytest.raises(ValueError, match=r"numnodes 2 is more than the preset's 1 host\(s\)"):
        _check(_cfg({"numnodes": 2}), LOCAL_DIRECT)


def test_numnodes_within_the_local_hosts_passes() -> None:
    from crab.core.execution.settings import from_preset

    settings = from_preset(
        {"scheduler": "local", "launcher": "mpirun", "hosts": ["a:4", "b:4"]}, "t"
    )
    assert _check(_cfg({"numnodes": 2, "ppn": 4}), settings) == []
    with pytest.raises(ValueError, match=r"numnodes 3 is more than the preset's 2 host\(s\)"):
        _check(_cfg({"numnodes": 3}), settings)


def test_sbatch_directives_under_a_local_preset_are_a_warning() -> None:
    from settings_fixtures import LOCAL_DIRECT, slurm_settings

    config = _cfg({"numnodes": 1, "sbatch_directives": ["--time=00:10:00"]})
    assert _check(config, LOCAL_DIRECT) == ["sbatch_directives are ignored by the local scheduler"]
    assert _check(config, slurm_settings()) == []
    assert _check(_cfg({"numnodes": 1, "sbatch_directives": []}), LOCAL_DIRECT) == []


def test_without_settings_the_preset_checks_are_skipped() -> None:
    config = _cfg({"numnodes": 4, "ppn": 4, "launcher": "direct"}, local={"launcher": "srun"})
    assert _check(config) == []


@pytest.mark.parametrize("value", ["{n}", None])
def test_token_or_missing_numnodes_and_ppn_are_skipped(value: object) -> None:
    from settings_fixtures import LOCAL_DIRECT

    global_options = {} if value is None else {"numnodes": value, "ppn": value}
    assert _check(_cfg(global_options), LOCAL_DIRECT) == []


# -- allocation.mode "shared" and `cores` (ADR-033) ---------------------------------------------


def _shared_check(allocation: dict, apps: object = 2) -> list[str]:
    from crab.core.config_checks import check_config

    return check_config(_alloc_config(allocation, apps, numnodes="4"))


@pytest.mark.parametrize("key,value", [("split", [50, 50]), ("stride", 2), ("seed", 7)])
def test_shared_refuses_split_stride_and_seed(key: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"experiment e1: allocation: {key}.*mode 'shared'"):
        _shared_check({"mode": "shared", key: value})


@pytest.mark.parametrize("key,value", [("split", [50, 50]), ("stride", 2), ("seed", 7)])
def test_shared_partition_refuses_split_stride_and_seed(key: str, value: object) -> None:
    alloc = {"partitions": {"victim": {"mode": "shared", key: value}, "aggressor": {}}}
    apps = [{"path": "a.py", "partition": "victim"}, {"path": "a.py", "partition": "aggressor"}]
    with pytest.raises(ValueError, match=rf"experiment e1: partition 'victim': {key}.*'shared'"):
        _shared_check(alloc, apps)


def test_cores_without_shared_is_refused() -> None:
    with pytest.raises(ValueError, match=r"experiment e1: allocation: cores.*mode 'shared'"):
        _shared_check({"mode": "linear", "cores": "shared"})
    with pytest.raises(ValueError, match=r"experiment e1: allocation: cores.*mode 'shared'"):
        _shared_check({"cores": "disjoint"})


def test_cores_in_a_partition_without_shared_is_refused() -> None:
    alloc = {"partitions": {"victim": {"cores": "shared"}, "aggressor": {}}}
    apps = [{"path": "a.py", "partition": "victim"}, {"path": "a.py", "partition": "aggressor"}]
    with pytest.raises(ValueError, match=r"experiment e1: partition 'victim': cores.*'shared'"):
        _shared_check(alloc, apps)


@pytest.mark.parametrize("cores", ["exclusive", "", 1, True])
def test_cores_must_be_disjoint_or_shared(cores: object) -> None:
    with pytest.raises(ValueError, match=r"experiment e1: allocation: cores .*disjoint, shared"):
        _shared_check({"mode": "shared", "cores": cores})


def test_top_level_shared_with_partitions_is_refused() -> None:
    alloc = {"mode": "shared", "partitions": {"victim": {}, "aggressor": {}}}
    apps = [{"path": "a.py", "partition": "victim"}, {"path": "a.py", "partition": "aggressor"}]
    with pytest.raises(ValueError, match=r"experiment e1: allocation\.mode 'shared'.*partitions"):
        _shared_check(alloc, apps)


@pytest.mark.parametrize(
    "allocation",
    [
        {"mode": "shared"},
        {"mode": "shared", "cores": "shared"},
        {"mode": "shared", "cores": "disjoint"},
    ],
)
def test_valid_shared_allocations_pass(allocation: dict) -> None:
    assert _shared_check(allocation) == []


def test_valid_shared_partition_passes() -> None:
    alloc = {
        "partitions": {
            "victim": {"share": 50, "mode": "shared", "cores": "disjoint"},
            "aggressor": {"share": 50},
        }
    }
    apps = [{"path": "a.py", "partition": "victim"}, {"path": "a.py", "partition": "aggressor"}]
    assert _shared_check(alloc, apps) == []


@pytest.mark.parametrize(
    "allocation",
    [
        {"mode": "shared", "cores": "{c}"},
        {"mode": "shared", "split": "{s}"},
        {"mode": "shared", "stride": "{k}"},
        {"mode": "shared", "seed": "{seed}"},
        {"mode": "{m}", "cores": "shared"},
        {"mode": "linear", "cores": "{c}"},
    ],
)
def test_tokens_in_shared_fields_are_skipped(allocation: dict) -> None:
    assert _shared_check(allocation) == []


# -- app `cpus` and `env`: placement of co-located apps (ADR-033) -----------------------------------
# Before: both keys were injected into the wrapper as attributes and never checked, so two apps on
# one node could pin the same cores and an invalid env surfaced as a launch failure.


def _placed_config(apps: list[dict], allocation: dict | None = None, ppn: int = 4) -> dict:
    exp = {
        "apps": {str(i): {"path": "a.py", **app} for i, app in enumerate(apps)},
        "local_options": {"allocation": allocation or {"mode": "shared"}},
    }
    return {"global_options": {"numnodes": 1, "ppn": ppn}, "experiments": {"e1": exp}}


def _sixteen_core_host():
    from crab.core.execution.settings import from_preset

    return from_preset({"scheduler": "local", "hosts": ["box:16"], "launcher": "mpirun"}, "t")


def test_overlapping_cpus_of_co_located_apps_are_refused() -> None:
    config = _placed_config([{"cpus": "0-3"}, {"cpus": "2-5"}])
    with pytest.raises(ValueError, match=r"experiment e1: app 0 and app 1.*core\(s\) 2,3"):
        _check(config, _sixteen_core_host())


def test_an_automatic_core_split_is_a_warning() -> None:
    warnings = _check(_placed_config([{}, {}]), _sixteen_core_host())
    assert len(warnings) == 1
    assert "automatic disjoint core split" in warnings[0]


def test_invalid_app_env_is_refused_with_the_experiment_and_app() -> None:
    config = _placed_config([{}, {"env": {"CRAB_MPIRUN": "mpirun"}}])
    with pytest.raises(ValueError, match="experiment e1: app 1: env: CRAB_MPIRUN"):
        _check(config, _sixteen_core_host())


def test_invalid_app_nics_is_refused_with_the_experiment_and_app() -> None:
    config = _placed_config([{}, {"nics": ["mlx5 0:1"]}])
    with pytest.raises(ValueError, match="experiment e1: app 1: nics: 'mlx5 0:1' is not a NIC"):
        _check(config, _sixteen_core_host())


def test_nics_with_ucx_net_devices_is_refused_with_the_experiment_and_app() -> None:
    both = {"nics": ["mlx5_0:1"], "env": {"UCX_NET_DEVICES": "mlx5_1:1"}}
    config = _placed_config([{}, both])
    with pytest.raises(ValueError, match="experiment e1: app 1: nics and env UCX_NET_DEVICES"):
        _check(config, _sixteen_core_host())


def test_nics_is_a_placement_key_so_a_token_skips_the_placement_check() -> None:
    config = _placed_config([{"nics": "{n}"}])
    assert _check(config, _sixteen_core_host()) == []


def test_placement_is_skipped_without_settings_and_for_tokens() -> None:
    config = _placed_config([{"cpus": "0-3"}, {"cpus": "2-5"}])
    assert _check(config) == []
    config["global_options"]["ppn"] = "{p}"
    assert _check(config, _sixteen_core_host()) == []
    token_cpus = _placed_config([{"cpus": "{c}"}, {"cpus": "2-5"}])
    assert _check(token_cpus, _sixteen_core_host()) == []


def test_a_cpus_the_launcher_ignores_is_a_warning_in_check_config() -> None:
    config = _placed_config([{"cpus": "0-3"}], ppn=1)
    config["global_options"]["launcher"] = "direct"
    warnings = _check(config, _sixteen_core_host())
    assert warnings == [
        "experiment e1: app 0: cpus has no effect with the direct launcher; "
        "only mpirun binds to an explicit core list"
    ]
    del config["global_options"]["launcher"]
    assert _check(config, _sixteen_core_host()) == []


_LEGACY_ALLOCATION = {
    "allocationmode": "p",
    "partitionsplit": "50:50",
    "partitionlayout": "i",
    "allocationsplit": "100-100",
}


def test_legacy_allocation_keys_are_refused_with_the_new_form() -> None:
    with pytest.raises(ValueError) as exc:
        _run(global_options=dict(_LEGACY_ALLOCATION))
    message = str(exc.value)
    for key in _LEGACY_ALLOCATION:
        assert key in message
    assert "allocation" in message
    assert "interleaved" in message
    assert "partitions" in message
    assert "partition" in message


def test_legacy_allocation_key_in_local_options_names_the_experiment() -> None:
    with pytest.raises(ValueError, match=r"e1.*partitionsplit"):
        _run(local={"partitionsplit": "50:50"})


def test_modern_allocation_block_passes_without_legacy_keys() -> None:
    _passes_checks(global_options={"allocation": {"mode": "interleaved"}})


# --- Silent data loss and ignored keys ----------------------------------------------------
# Before: a collected app stopped by `end` ("f" or a time) had its output skipped without a
# message (collect_run skips a non-zero returncode), and keys the engine reads only once for
# the whole job (or not at all) were silently ignored at experiment level.


def _warnings_for(apps: dict | None = None, local: dict | None = None, **exp_keys: object):
    from crab.core.config_checks import check_config

    exp: dict = {"apps": apps if apps is not None else {"0": {"path": "a.py"}}, **exp_keys}
    if local is not None:
        exp["local_options"] = local
    return check_config({"global_options": {}, "experiments": {"e1": exp}})


@pytest.mark.parametrize("end", ["f", "60", 60])
def test_collected_app_stopped_by_end_is_warned_about(end: object) -> None:
    warnings = _warnings_for(apps={"7": {"path": "a.py", "collect": True, "end": end}})
    assert len(warnings) == 1
    assert "experiment e1: app 7" in warnings[0]
    assert "not collected" in warnings[0]
    assert "still running" in warnings[0]


def test_string_collect_true_is_warned_about_too() -> None:
    warnings = _warnings_for(apps={"0": {"path": "a.py", "collect": "true", "end": "f"}})
    assert len(warnings) == 1
    assert "experiment e1: app 0" in warnings[0]


@pytest.mark.parametrize(
    "app",
    [
        {"path": "a.py", "collect": True, "end": ""},
        {"path": "a.py", "collect": True},
        {"path": "a.py", "collect": False, "end": "f"},
        {"path": "a.py", "end": "f"},
        {"path": "a.py", "collect": "false", "end": "60"},
    ],
)
def test_other_collect_end_combinations_are_not_warned_about(app: dict) -> None:
    assert _warnings_for(apps={"0": app}) == []


@pytest.mark.parametrize("key", ["numnodes", "walltime", "sbatch_directives"])
def test_job_level_key_in_local_options_is_warned_about(key: str) -> None:
    value = {"--qos": "x"} if key == "sbatch_directives" else "4"
    warnings = _warnings_for(local={key: value})
    assert len(warnings) == 1
    assert "experiment e1" in warnings[0]
    assert f"local_options.{key}" in warnings[0]
    assert "whole job" in warnings[0]
    assert "global_options" in warnings[0]


def test_unknown_experiment_level_key_is_warned_about() -> None:
    warnings = _warnings_for(sbatch_directives=["--qos=x"])
    assert len(warnings) == 1
    assert "experiment e1" in warnings[0]
    assert "sbatch_directives" in warnings[0]
    assert "ignored" in warnings[0]


def test_description_local_options_and_apps_are_allowed_in_an_experiment() -> None:
    assert _warnings_for(local={"retain_files": True}, description="a baseline") == []


def test_launcher_options_are_allowed_in_local_options() -> None:
    local = {"launcher": "mpirun", "launcher_flags": ["--bind-to", "core"]}
    assert _warnings_for(local=local) == []


# -- ppn per app and per experiment ---------------------------------------------------------------


def _ppn_cfg(app: dict | None = None, local: dict | None = None, **global_options: object) -> dict:
    exp: dict = {"apps": {"0": {"path": "a.py", **(app or {})}}}
    if local is not None:
        exp["local_options"] = local
    return {"global_options": {"numnodes": 1, **global_options}, "experiments": {"e1": exp}}


@pytest.mark.parametrize("value", [0, -2, "x", "0", 2.5, True, None])
def test_an_app_ppn_must_be_a_positive_integer(value: object) -> None:
    config = _ppn_cfg(app={"ppn": value})
    with pytest.raises(ValueError, match=r"experiment e1: app 0: ppn must be a positive integer"):
        _check(config)


@pytest.mark.parametrize("value", [0, "x", True])
def test_a_local_options_ppn_must_be_a_positive_integer(value: object) -> None:
    config = _ppn_cfg(local={"ppn": value})
    with pytest.raises(ValueError, match=r"experiment e1: local_options: ppn must be a positive"):
        _check(config)


@pytest.mark.parametrize("value", [1, 4, "4", "{p}"])
def test_a_ppn_number_or_token_passes_on_an_app_and_in_local_options(value: object) -> None:
    assert _check(_ppn_cfg(app={"ppn": value})) == []
    assert _check(_ppn_cfg(local={"ppn": value})) == []


@pytest.mark.parametrize("value", [0, "x", None])
def test_a_global_ppn_must_be_a_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match=r"global_options: ppn must be a positive integer"):
        _check(_ppn_cfg(ppn=value))


@pytest.mark.parametrize("value", [4, "4", "{p}"])
def test_a_global_ppn_number_or_token_passes(value: object) -> None:
    assert _check(_ppn_cfg(ppn=value)) == []


def test_local_options_ppn_is_no_longer_called_ignored_but_numnodes_still_is() -> None:
    assert _warnings_for(local={"ppn": 2}) == []
    assert len(_warnings_for(local={"numnodes": 2})) == 1


def test_direct_launcher_checks_the_effective_ppn_of_every_app() -> None:
    from settings_fixtures import LOCAL_DIRECT

    message = r"experiment e1: .*direct launcher runs a single process"
    with pytest.raises(ValueError, match=message):
        _check(_ppn_cfg(app={"ppn": 2}), LOCAL_DIRECT)
    with pytest.raises(ValueError, match=message):
        _check(_ppn_cfg(local={"ppn": 2}), LOCAL_DIRECT)
    # An app's own ppn beats the larger global one.
    assert _check(_ppn_cfg(app={"ppn": 1}, ppn=4), LOCAL_DIRECT) == []


def test_placement_checks_use_the_apps_effective_ppn() -> None:
    from settings_fixtures import slurm_settings

    settings = slurm_settings()
    directives = ["--exclusive"]
    config = _ppn_cfg(app={"cpus": "0-1"}, local={"ppn": 4}, sbatch_directives=directives)
    with pytest.raises(ValueError, match=r"experiment e1: app 0: cpus lists 2 core"):
        _check(config, settings)
    # An app's own ppn beats the larger global one: the two cores are enough, nothing to warn about.
    config = _ppn_cfg(app={"cpus": "0-1", "ppn": 2}, ppn=4, sbatch_directives=directives)
    assert _check(config, settings) == []
    # ... and an app ppn beyond its cores is refused although the global ppn would fit.
    config = _ppn_cfg(app={"cpus": "0-1", "ppn": 3}, ppn=1, sbatch_directives=directives)
    with pytest.raises(ValueError, match=r"experiment e1: app 0: cpus lists 2 core.*ppn 3"):
        _check(config, settings)


@pytest.mark.parametrize(
    ("flags", "reported"),
    [
        (["--mpi=pmix --cpu-bind=cores"], "--cpu-bind=cores"),
        (["--cpu-bind cores"], "--cpu-bind"),
        (["--mpi=pmix", "--cpu_bind=socket --exact"], "--cpu_bind=socket"),
    ],
)
def test_check_config_warns_about_a_cpu_bind_inside_a_multi_word_launcher_flag(
    flags: list[str], reported: str
) -> None:
    from settings_fixtures import slurm_settings

    config = {
        "global_options": {"numnodes": 1, "ppn": 1},
        "experiments": {
            "e1": {
                "apps": {"0": {"path": "a.py"}, "1": {"path": "b.py"}},
                "local_options": {"allocation": {"mode": "shared"}},
            }
        },
    }
    warnings = _check(config, slurm_settings(launchers={"srun": {"flags": flags}}))
    assert [w for w in warnings if "core binding" in w] == [
        f"experiment e1: launcher flag '{reported}' is replaced by CRAB's core binding "
        "for apps that share nodes or set cpus"
    ]
