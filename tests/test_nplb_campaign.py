"""The contention-campaign block generator (examples/nplb/campaign.py) and its cost model
(examples/nplb/campaign_cost.py).

One block is a (layout, victim) pair holding one experiment per condition. The blocks are put
through CRAB's real checks (`check_config` with the H100 GPU preset) and its real node allocator and
placement code on four fake nodes. Nothing here starts a process or sends a signal.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
H100 = NPLB / "h100"
GPU_PRESET_FILE = NPLB / "presets" / "nplb_h100_gpu.json"


def _load(name: str) -> ModuleType:
    """Import a script from examples/nplb (not a package) under its own file name."""
    if str(NPLB) not in sys.path:
        sys.path.insert(0, str(NPLB))
    spec = importlib.util.spec_from_file_location(name, NPLB / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


campaign_cost = _load("campaign_cost")
campaign = _load("campaign")

LAYOUT_NAMES = ["split-i", "split-l", "shared"]
VICTIM_NAMES = ["a2a", "agtr", "ardc"]
CONDITION_NAMES = [
    "alone",
    "steady_a128k",
    "steady_a2m",
    "steady_a16m",
    "burst_l1ms_p1ms_a128k",
    "burst_l10ms_p10ms_a128k",
    "burst_l100ms_p100ms_a128k",
    "burst_l10ms_p30ms_a128k",
    "burst_l30ms_p10ms_a128k",
    "burst_l10ms_p10ms_a2m",
    "burst_l10ms_p10ms_a16m",
]
SIZES = [16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864, 268435456]
SIZE_ARGS = {"steady_a128k": 131072, "steady_a2m": 2097152, "steady_a16m": 16777216}
PROGRAMS = {
    "a2a": ("blink/alltoall/alltoall_comm_only.py", "alltoall_comm_only"),
    "agtr": ("blink/allgather/allgather_comm_only.py", "allgather_comm_only"),
    "ardc": ("blink/allreduce/allreduce_b.py", "allreduce_b"),
}
BIN = "~/crab/lab/blink-gpu/build-cuda/bin"
GPU_ENV = {
    "UCX_MAX_RNDV_RAILS": "1",
    "UCX_IB_ROCE_REACHABILITY_MODE": "all",
    "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
}
NICS = [
    "mlx5_0:1",
    "mlx5_2:1",
    "mlx5_4:1",
    "mlx5_8:1",
    "mlx5_10:1",
    "mlx5_12:1",
    "mlx5_14:1",
    "mlx5_16:1",
]
NODES = ["n1", "n2", "n3", "n4"]
LOCAL_CONDITIONS = ["alone", "steady_a128k", "burst_l10ms_p10ms_a128k"]


def apps_of(config: dict[str, Any], exp: str) -> list[dict[str, Any]]:
    apps = config["experiments"][exp]["apps"]
    return [apps[k] for k in sorted(apps, key=int)]


def option(app: dict[str, Any], flag: str) -> str:
    words = app["args"].split()
    return words[words.index(flag) + 1]


def block(layout: str, victim: str, **kwargs: Any) -> dict[str, Any]:
    stem, config = campaign.build_block(layout, victim, **kwargs)
    assert isinstance(stem, str)
    return config  # type: ignore[no-any-return]


# --- the cost model ----------------------------------------------------------------------------


def test_cost_table_matches_the_october_data() -> None:
    for victim in ("a2a", "agtr"):
        times = [campaign_cost.iter_time(victim, size, "split-i", False) for size in SIZES]
        assert [s for _, s in times][:7] == ["measured"] * 7
        values = [t for t, _ in times]
        assert all(t > 0 for t in values)
        assert 0.01 < values[-1] < 2.0  # 256 MiB, in seconds: the units are right
    a2a = [campaign_cost.iter_time("a2a", size, "split-i", False)[0] for size in SIZES]
    assert a2a == sorted(a2a) and len(set(a2a)) == 8
    # Spot values from the tarballs (median over samples of the slowest rank, ppn 8).
    assert campaign_cost.iter_time("a2a", 1048576, "split-l", False) == (
        pytest.approx(0.000306709),
        "measured",
    )
    assert campaign_cost.iter_time("agtr", 67108864, "split-l", False) == (
        pytest.approx(0.02165393),
        "measured",
    )
    # The October a2a run failed at 256 MiB (memory registration): that one point is extrapolated.
    assert campaign_cost.iter_time("a2a", 268435456, "split-i", False)[1] == "extrapolated"
    assert campaign_cost.iter_time("agtr", 268435456, "split-i", False)[1] == "measured"


def test_cost_sources_ardc_proxy_and_contention() -> None:
    f = campaign_cost
    for size in SIZES:
        base, _ = f.iter_time("a2a", size, "split-i", False)
        assert f.iter_time("ardc", size, "split-i", False) == (
            pytest.approx(f.ALLREDUCE_FACTOR * base),
            "model-ardc",
        )
    assert f.ALLREDUCE_FACTOR == 2.0 and f.CONTENTION_FACTOR == 2.0
    base, _ = f.iter_time("agtr", 1048576, "split-i", False)
    t, source = f.iter_time("agtr", 1048576, "split-l", True)
    assert t == pytest.approx(f.CONTENTION_FACTOR * base)
    assert source == "assumed-contention"
    t, source = f.iter_time("agtr", 1048576, "shared", False)
    assert (t, source) == (pytest.approx(base), "proxy-ppn8")
    t, source = f.iter_time("agtr", 1048576, "shared", True)
    assert t == pytest.approx(f.CONTENTION_FACTOR * base)
    assert source == "proxy-ppn8+assumed-contention"
    t, source = f.iter_time("ardc", 1048576, "split-i", True)
    assert source == "model-ardc+assumed-contention"
    a2a_1m, _ = f.iter_time("a2a", 1048576, "split-i", False)
    assert t == pytest.approx(f.ALLREDUCE_FACTOR * f.CONTENTION_FACTOR * a2a_1m)


def test_unknown_size_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="1024"):
        campaign_cost.iter_time("a2a", 1024, "split-i", False)


def test_iterations_clamp() -> None:
    f = campaign_cost
    # Slow victim: an iteration takes 62 ms at 256 MiB (a2a) -> 20 s / t is about 319, in range.
    t, _ = f.iter_time("a2a", 268435456, "split-i", False)
    n, warmup, source = f.iterations("a2a", 268435456, "split-i", False)
    assert n == round(20.0 / t) and warmup == max(10, n // 10) and source == "extrapolated"
    # Tiny t clamps at 20000, huge t at 100 (the table's own extremes plus a patched value).
    n, warmup, _ = f.iterations("a2a", 16384, "split-i", False)
    assert (n, warmup) == (20000, 2000)
    n, warmup, _ = f.iterations("a2a", 16384, "split-i", False, target_s=100000.0)
    assert (n, warmup) == (20000, 2000)
    n, warmup, _ = f.iterations("agtr", 268435456, "shared", True, target_s=1.0)
    assert (n, warmup) == (100, 10)
    n, warmup, _ = f.iterations("a2a", 16384, "split-i", False, target_s=1e-9)
    assert (n, warmup) == (100, 10)
    # In between: warmup is a tenth of the iterations, never below 10.
    n, warmup, _ = f.iterations("agtr", 1048576, "split-i", False, target_s=10.0)
    assert n == round(10.0 / f.iter_time("agtr", 1048576, "split-i", False)[0])
    assert warmup == max(10, n // 10)
    assert f.iterations("agtr", 1048576, "split-i", True)[2] == "assumed-contention"


# --- the blocks --------------------------------------------------------------------------------


@pytest.mark.parametrize("victim", VICTIM_NAMES)
@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_blocks_cover_victims_layouts_conditions(layout: str, victim: str) -> None:
    stem, config = campaign.build_block(layout, victim)
    assert stem == f"{layout}_{victim}"
    assert config["schema_version"] == 2
    opts = config["global_options"]
    assert opts["name"] == f"nplb_{layout}_{victim}"
    assert opts["numnodes"] == "4"
    assert opts["minruns"] == "1" and opts["maxruns"] == "1" and opts["outformat"] == "csv"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", opts["walltime"])
    hours, minutes, seconds = (int(x) for x in opts["walltime"].split(":"))
    total = hours * 3600 + minutes * 60 + seconds
    assert 30 * 60 <= total <= 6 * 3600
    assert re.fullmatch(r"\d+\.0", opts["timeout"]) and float(opts["timeout"]) >= 600.0
    assert list(config["experiments"]) == CONDITION_NAMES
    for body in config["experiments"].values():
        assert body["description"].endswith(".") and "victim" in body["description"].lower()


@pytest.mark.parametrize("victim", VICTIM_NAMES)
@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_walltime_and_timeout_follow_the_documented_formula(layout: str, victim: str) -> None:
    config = block(layout, victim)
    contended_of = lambda exp: exp != "alone"  # noqa: E731
    per_experiment: list[float] = []
    for exp in config["experiments"]:
        apps = apps_of(config, exp)
        seconds = 0.0
        for app in apps:
            seconds += 10.0
            if app["collect"]:  # the victim apps; the aggressor runs inside their time
                size = int(option(app, "-msgsize"))
                t, _ = campaign_cost.iter_time(victim, size, layout, contended_of(exp))
                seconds += (int(option(app, "-iter")) + int(option(app, "-warmup"))) * t
        per_experiment.append(seconds)
    want = max(30 * 60, math.ceil(1.5 * sum(per_experiment)))
    h, m, s = want // 3600, want % 3600 // 60, want % 60
    assert config["global_options"]["walltime"] == f"{h:02d}:{m:02d}:{s:02d}"
    timeout = float(max(600, math.ceil(5 * max(per_experiment))))
    assert config["global_options"]["timeout"] == f"{timeout}"


def test_a_block_over_the_walltime_cap_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(campaign, "WALLTIME_CAP_S", 60)
    with pytest.raises(campaign.UsageError) as caught:
        campaign.build_block("split-i", "a2a")
    message = str(caught.value)
    assert "split-i_a2a" in message and "hours" in message


@pytest.mark.parametrize("victim", VICTIM_NAMES)
@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_victim_chain_and_flags(layout: str, victim: str) -> None:
    wrapper, program = PROGRAMS[victim]
    config = block(layout, victim)
    for exp in config["experiments"]:
        apps = apps_of(config, exp)
        victims = apps[:8]
        assert [int(option(a, "-msgsize")) for a in victims] == SIZES, exp
        assert victims[0]["start"] == ("0" if exp == "alone" else "3"), exp
        for k, app in enumerate(victims[1:]):
            assert app["start"] == f"s{k}", exp
            assert "cpus" not in app and "partition" not in app, exp
        keys = list(config["experiments"][exp]["apps"])
        assert keys == [str(i) for i in range(len(keys))], exp
        for app in victims:
            size = int(option(app, "-msgsize"))
            n, warmup, _ = campaign_cost.iterations(victim, size, layout, exp != "alone")
            assert app["args"] == (
                f"-msgsize {size} -iter {n} -warmup {warmup} -grty 1 -maxsamples {n} -gpu"
            )
            assert app["path"] == wrapper and app["binary"] == f"{BIN}/{program}"
            assert app["collect"] is True
            assert app["end"] == ""
            assert size % 4 == 0


@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_aggressor_flags(layout: str) -> None:
    config = block(layout, "a2a")
    assert len(apps_of(config, "alone")) == 8
    assert all(a["collect"] for a in apps_of(config, "alone"))
    bursts = {
        "burst_l1ms_p1ms_a128k": (131072, "1ms", "1ms"),
        "burst_l10ms_p10ms_a128k": (131072, "10ms", "10ms"),
        "burst_l100ms_p100ms_a128k": (131072, "100ms", "100ms"),
        "burst_l10ms_p30ms_a128k": (131072, "10ms", "30ms"),
        "burst_l30ms_p10ms_a128k": (131072, "30ms", "10ms"),
        "burst_l10ms_p10ms_a2m": (2097152, "10ms", "10ms"),
        "burst_l10ms_p10ms_a16m": (16777216, "10ms", "10ms"),
    }
    for exp in config["experiments"]:
        if exp == "alone":
            continue
        apps = apps_of(config, exp)
        assert len(apps) == 9, exp
        agg = apps[-1]
        assert list(config["experiments"][exp]["apps"])[-1] == "8", exp
        if exp in SIZE_ARGS:
            assert agg["args"] == f"-msgsize {SIZE_ARGS[exp]} -endl -warmup 0 -grty 1 -gpu", exp
        else:
            size, length, pause = bursts[exp]
            assert agg["args"] == (
                f"-msgsize {size} -endl -warmup 0 -grty 1 -blength {length} -bpause {pause} -gpu"
            ), exp
        words = agg["args"].split()
        assert "-iter" not in words and "-maxsamples" not in words
        assert agg["collect"] is False
        assert (agg["start"], agg["end"]) == ("0", "f")
        assert agg["path"] == "blink/alltoall/alltoall_comm_only.py"
        assert agg["binary"] == f"{BIN}/alltoall_comm_only"
        if layout == "shared":
            assert "partition" not in agg
        else:
            assert agg["partition"] == "aggressor"


def test_split_layouts_allocation() -> None:
    partitions = {"victim": {"share": 50}, "aggressor": {"share": 50}}
    for layout, mode in (("split-i", "interleaved"), ("split-l", "linear")):
        config = block(layout, "agtr")
        opts = config["global_options"]
        assert opts["allocation"] == {"mode": mode, "partitions": partitions}
        assert opts["ppn"] == "8"
        assert "--exclusive" in opts["sbatch_directives"]
        assert f"--job-name=nplb_{layout}_agtr" in opts["sbatch_directives"]
        assert "--nodelist" not in json.dumps(config)
        for exp in config["experiments"]:
            apps = apps_of(config, exp)
            assert apps[0]["partition"] == "victim" and apps[0]["ppn"] == 8
            assert all(a["ppn"] == 8 for a in apps)
            if exp != "alone":
                assert apps[-1]["partition"] == "aggressor"
    # The first `ppn` entries of the arrays, as gpu_set.py pins them.
    config = block("split-l", "a2a")
    apps = apps_of(config, "steady_a2m")
    assert apps[0]["cpus"] == "0,1,2,3,40,41,42,43" and apps[0]["nics"] == NICS
    assert apps[-1]["cpus"] == "0,1,2,3,40,41,42,43" and apps[-1]["nics"] == NICS
    assert apps[-1]["env"] == GPU_ENV and apps[0]["env"] == GPU_ENV


@pytest.mark.parametrize("victim", VICTIM_NAMES)
def test_shared_layout_pins_disjoint_gpus_nics_cores(victim: str) -> None:
    config = block("shared", victim)
    opts = config["global_options"]
    assert opts["allocation"] == {"mode": "shared"}
    assert opts["numnodes"] == "4" and opts["ppn"] == "4"
    assert "partition" not in json.dumps(config)
    assert "--exclusive" in opts["sbatch_directives"]
    for exp in config["experiments"]:
        apps = apps_of(config, exp)
        for app in apps[:8]:
            assert app["ppn"] == 4 and app["nics"] == NICS[0:4], exp
            assert app["env"] == GPU_ENV, exp  # GPUs 0-3 by local rank: no CUDA_VISIBLE_DEVICES
        assert apps[0]["cpus"] == "0,1,2,3"
        if exp == "alone":
            continue
        agg = apps[-1]
        assert agg["ppn"] == 4
        assert agg["env"] == {**GPU_ENV, "CUDA_VISIBLE_DEVICES": "4,5,6,7"}
        assert agg["nics"] == NICS[4:8] and agg["cpus"] == "40,41,42,43"
        victim_gpus = set(range(4))
        aggressor_gpus = {int(g) for g in agg["env"]["CUDA_VISIBLE_DEVICES"].split(",")}
        assert victim_gpus.isdisjoint(aggressor_gpus)
        assert set(apps[0]["nics"]).isdisjoint(agg["nics"])
        victim_cores = {int(c) for c in apps[0]["cpus"].split(",")}
        assert victim_cores.isdisjoint(int(c) for c in agg["cpus"].split(","))


# --- CRAB's own checks, allocator and placement --------------------------------------------------


def _gpu_settings() -> Any:
    from crab.cli.presets import resolve_presets
    from crab.core.execution.settings import from_preset

    resolved = resolve_presets([], [(GPU_PRESET_FILE, json.loads(GPU_PRESET_FILE.read_text()))])
    body = dict(resolved["nplb_h100_gpu"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100_gpu")


def _shape(message: str) -> str:
    """A warning without its experiment name."""
    return re.sub(r"experiment \S+:", "experiment X:", message)


def test_every_block_validates_with_crab(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config

    settings = _gpu_settings()
    committed: set[str] = set()
    for path in sorted(H100.glob("gpu_*.json")):
        committed.update(_shape(w) for w in check_config(json.loads(path.read_text()), settings))
    for layout in LAYOUT_NAMES:
        for victim in VICTIM_NAMES:
            stem, config = campaign.build_block(layout, victim)
            path = tmp_path / f"{stem}.json"
            path.write_text(json.dumps(config, indent=2) + "\n")
            warnings = check_config(json.loads(path.read_text()), settings)
            assert {_shape(w) for w in warnings} <= committed, (stem, warnings)


class _Stub:
    """What the node allocator reads from an app."""

    def __init__(self, partition: str | None, start: str) -> None:
        self.partition_id = partition
        self.start_string = start
        self.nodes: list[str] = []

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)


def _nodes_of(config: dict[str, Any], exp: str) -> list[list[str]]:
    """Nodes of each app, from CRAB's real allocator and placement code."""
    from crab.core.allocation.allocator import NodeAllocator
    from crab.core.execution.placement import place_experiment

    opts = config["global_options"]
    apps = config["experiments"][exp]["apps"]
    keys = list(apps)
    cfgs = [apps[k] for k in keys]
    stubs = [_Stub(c.get("partition"), c["start"]) for c in cfgs]
    NodeAllocator.allocate_experiment(stubs, list(NODES), opts["allocation"])
    nodes = [s.nodes for s in stubs]
    place_experiment(
        exp, keys, cfgs, nodes, opts["allocation"], opts, _gpu_settings(), NODES, opts
    )  # raises on a bad cpus/env/nics/ppn or a core overlap
    return nodes


def test_placement_through_the_real_allocator() -> None:
    expected = {
        "split-i": (["n1", "n3"], ["n2", "n4"]),
        "split-l": (["n1", "n2"], ["n3", "n4"]),
        "shared": (NODES, NODES),
    }
    for layout, (victim_nodes, aggressor_nodes) in expected.items():
        config = block(layout, "agtr")
        for exp in ("alone", "steady_a2m", "burst_l10ms_p30ms_a128k"):
            nodes = _nodes_of(config, exp)
            assert all(n == victim_nodes for n in nodes[:8]), (layout, exp)
            if exp != "alone":
                assert nodes[8] == aggressor_nodes, (layout, exp)


# --- names, CLI and local mode -------------------------------------------------------------------


def test_unknown_names_are_clear_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(campaign.UsageError, match="split-i, split-l, shared"):
        campaign.build_block("nope", "a2a")
    with pytest.raises(campaign.UsageError, match="a2a, agtr, ardc"):
        campaign.build_block("split-i", "nope")
    with pytest.raises(campaign.UsageError, match="alone, steady_a128k"):
        campaign.build_block("split-i", "a2a", conditions=["nope"])
    out = tmp_path / "local" / "c"
    for args in (["--layout", "x"], ["--victim", "x"], ["--conditions", "alone,x"]):
        assert campaign.main(["--out", str(out), *args]) == 2
        err = capsys.readouterr().err
        assert len(err.strip().splitlines()) == 1 and "campaign.py: error:" in err
    assert not out.exists()


def test_cli_writes_block_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "local" / "campaign"
    assert campaign.main(["--out", str(out), "--layout", "split-i", "--victim", "a2a"]) == 0
    stdout = capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == ["split-i_a2a.json"]
    lines = stdout.strip().splitlines()
    assert len(lines) == 1
    assert "split-i_a2a.json" in lines[0] and "11 experiments" in lines[0]
    assert "98 apps" in lines[0]  # 8 victim apps in `alone`, 8 + 1 aggressor in the other ten
    text = (out / "split-i_a2a.json").read_text()
    assert text.endswith("}\n")
    _, config = campaign.build_block("split-i", "a2a")
    assert text == json.dumps(config, indent=2) + "\n"
    assert list(json.loads(text)) == ["schema_version", "global_options", "experiments"]
    # Deterministic: a second run overwrites with the same bytes.
    assert campaign.main(["--out", str(out), "--layout", "split-i", "--victim", "a2a"]) == 0
    assert (out / "split-i_a2a.json").read_text() == text


def test_cli_without_filters_writes_all_nine_and_honors_sizes_and_conditions(
    tmp_path: Path,
) -> None:
    out = tmp_path / "local" / "c"
    assert campaign.main(["--out", str(out)]) == 0
    assert sorted(p.stem for p in out.glob("*.json")) == sorted(
        f"{lay}_{vic}" for lay in LAYOUT_NAMES for vic in VICTIM_NAMES
    )
    small = tmp_path / "local" / "small"
    argv = ["--out", str(small), "--layout", "shared", "--victim", "ardc"]
    argv += ["--sizes", "1M,16384", "--conditions", "steady_a2m,alone"]
    assert campaign.main(argv) == 0
    config = json.loads((small / "shared_ardc.json").read_text())
    assert list(config["experiments"]) == ["alone", "steady_a2m"]  # the canonical order
    assert [int(option(a, "-msgsize")) for a in apps_of(config, "alone")] == [16384, 1048576]


def test_cli_out_outside_a_local_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "plain"
    assert campaign.main(["--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "local/" in err
    assert not out.exists()


def test_local_mode(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config
    from settings_fixtures import slurm_settings

    out = tmp_path / "local" / "c"
    assert campaign.main(["--out", str(out), "--local"]) == 0
    files = sorted(p.name for p in out.glob("*.json"))
    assert files == sorted(
        f"local_{lay}_{vic}.json" for lay in ("split-i", "shared") for vic in VICTIM_NAMES
    )
    for name in files:
        config = json.loads((out / name).read_text())
        text = json.dumps(config)
        for word in ('"binary"', "-gpu", '"nics"', '"cpus"', "CUDA_", "UCX_", "--exclusive"):
            assert word not in text, (name, word)
        opts = config["global_options"]
        assert opts["numnodes"] == "2" and opts["ppn"] == "2"
        assert list(config["experiments"]) == LOCAL_CONDITIONS
        shared = "_shared_" in name
        if shared:
            assert opts["allocation"] == {"mode": "shared"}
        else:
            assert opts["allocation"] == {
                "mode": "interleaved",
                "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}},
            }
        for exp in config["experiments"]:
            apps = apps_of(config, exp)
            victims = [a for a in apps if a["collect"]]
            assert [int(option(a, "-msgsize")) for a in victims] == [1024, 1048576]
            for app in victims:
                assert option(app, "-iter") == "5" and option(app, "-warmup") == "1"
                assert app["ppn"] == (1 if shared else 2)
        agg = apps_of(config, "burst_l10ms_p10ms_a128k")[-1]
        assert agg["args"] == ("-msgsize 131072 -endl -warmup 0 -grty 1 -blength 10ms -bpause 10ms")
        assert check_config(config, slurm_settings()) == [], name
