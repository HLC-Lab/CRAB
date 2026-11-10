"""The lab smoke test (examples/nplb/smoke.py): five small blocks and their `schedule.tsv`.

The CLI is run as a subprocess (it is the contract) and the blocks are put through CRAB's real
`check_config` and node allocator. Expected layouts, victims and conditions are literals here, never
read back from `smoke.py`. Nothing starts a job or sends a signal; every file goes under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SMOKE_PY = NPLB / "smoke.py"
GPU_PRESET_FILE = NPLB / "presets" / "nplb_h100_gpu.json"
H100 = NPLB / "h100"
LOCAL_MESSAGE = "--out must be inside a local/ directory"
NODES = ["n1", "n2", "n3", "n4"]
BIN = "~/crab/lab/blink-gpu/build-cuda/bin"
# feature -> (layout, victim program path, condition names), in the order of `smoke.py features`
FEATURES = {
    "plain": ("split-i", "blink/alltoall/alltoall_comm_only.py", ["alone", "steady_a128k"]),
    "bursty": ("split-i", "blink/alltoall/alltoall_comm_only.py", ["burst_l10ms_p10ms_a128k"]),
    "ardc": ("split-i", "blink/allreduce/allreduce_b.py", ["alone", "steady_a128k"]),
    "split-l": ("split-l", "blink/alltoall/alltoall_comm_only.py", ["alone", "steady_a128k"]),
    "shared": ("shared", "blink/alltoall/alltoall_comm_only.py", ["alone", "steady_a128k"]),
}
HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)


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
campaign_plan = _load("campaign_plan")
run_log = _load("run_log")


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "WORK"):
        env.pop(var, None)
    env.update(extra)
    return env


def run_smoke(
    tmp_path: Path, *args: str, python: str = sys.executable, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python, "-I", str(SMOKE_PY), *args],
        env=env_for(tmp_path, **env),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )


def snapshot(folder: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
    }


@pytest.fixture
def out(tmp_path: Path) -> Path:
    """A folder under a `local/` directory with all five blocks planned into it."""
    folder = tmp_path / "local" / "smoke"
    result = run_smoke(tmp_path, "plan", "--out", str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    return folder


def block_of(folder: Path, feature: str) -> dict[str, Any]:
    config: dict[str, Any] = json.loads((folder / f"smoke_{feature}.json").read_text())
    return config


def apps_of(config: dict[str, Any], exp: str) -> list[dict[str, Any]]:
    apps = config["experiments"][exp]["apps"]
    return [apps[k] for k in sorted(apps, key=int)]


def option(app: dict[str, Any], flag: str) -> str:
    words = app["args"].split()
    return words[words.index(flag) + 1]


def victims_of(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for exp in config["experiments"] for a in apps_of(config, exp) if a["collect"]]


def aggressors_of(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for exp in config["experiments"] for a in apps_of(config, exp) if not a["collect"]]


# --- the five blocks -----------------------------------------------------------------------------


def test_the_five_blocks_cover_the_features(out: Path) -> None:
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["schedule.tsv", *(f"smoke_{f}.json" for f in FEATURES)]
    )
    for feature, (layout, program, conditions) in FEATURES.items():
        config = block_of(out, feature)
        assert list(config["experiments"]) == conditions, feature
        victims = victims_of(config)
        assert {a["path"] for a in victims} == {program}, feature
        assert len(victims) == 2 * len(conditions), feature  # two sizes per experiment
        opts = config["global_options"]
        assert opts["numnodes"] == "4"
        assert opts["name"] == f"nplb_smoke_{feature}"
        assert opts["sbatch_directives"] == ["--exclusive", f"--job-name=nplb_smoke_{feature}"]
        allocation = opts["allocation"]
        if layout == "shared":
            assert allocation == {"mode": "shared"}  # CRAB refuses `shared` with partitions
        else:
            assert allocation["mode"] == ("linear" if layout == "split-l" else "interleaved")
            assert set(allocation["partitions"]) == {"victim", "aggressor"}


def test_job_names_are_unique(out: Path) -> None:
    names = [block_of(out, f)["global_options"]["name"] for f in FEATURES]
    assert len(set(names)) == len(FEATURES)


def test_victims_have_two_sizes_and_twenty_iterations(out: Path) -> None:
    for feature in FEATURES:
        config = block_of(out, feature)
        for exp in config["experiments"]:
            victims = [a for a in apps_of(config, exp) if a["collect"]]
            assert [int(option(a, "-msgsize")) for a in victims] == [65536, 16777216], feature
            for app in victims:
                assert app["args"].startswith(f"-msgsize {option(app, '-msgsize')} -iter 20 ")
                assert option(app, "-iter") == "20"
                assert option(app, "-warmup") == "2"
                assert option(app, "-grty") == "1"
                assert option(app, "-maxsamples") == "20"
                assert "-gpu" in app["args"].split()


def test_aggressor_apps_are_what_build_block_gives_without_iters(out: Path) -> None:
    for feature, (layout, _, conditions) in FEATURES.items():
        victim = "ardc" if feature == "ardc" else "a2a"
        _, reference = campaign.build_block(layout, victim, conditions=conditions)
        config = block_of(out, feature)
        for exp in conditions:
            ours = [a for a in apps_of(config, exp) if not a["collect"]]
            theirs = [a for a in apps_of(reference, exp) if not a["collect"]]
            assert ours == theirs, (feature, exp)
            assert len(ours) == (0 if exp == "alone" else 1)


def test_bursty_block_has_a_bursty_gpu_aggressor(out: Path) -> None:
    config = block_of(out, "bursty")
    (aggressor,) = aggressors_of(config)
    assert option(aggressor, "-blength") == "10ms" and option(aggressor, "-bpause") == "10ms"
    words = aggressor["args"].split()
    assert "-endl" in words and "-gpu" in words
    assert option(aggressor, "-msgsize") == "131072"


def test_ardc_block_uses_allreduce_b_with_gpu_buffers(out: Path) -> None:
    config = block_of(out, "ardc")
    for app in victims_of(config):
        assert app["path"] == "blink/allreduce/allreduce_b.py"
        assert app["binary"] == f"{BIN}/allreduce_b"
        assert "-gpu" in app["args"].split()
    for app in aggressors_of(config):
        assert app["path"] == "blink/alltoall/alltoall_comm_only.py"


def test_split_l_is_linear_and_shared_pins_the_aggressor_apart(out: Path) -> None:
    assert block_of(out, "split-l")["global_options"]["allocation"]["mode"] == "linear"
    assert "interleaved" not in json.dumps(block_of(out, "split-l"))
    shared = block_of(out, "shared")
    (aggressor,) = aggressors_of(shared)
    assert aggressor["env"]["CUDA_VISIBLE_DEVICES"] == "4,5,6,7"
    assert all("CUDA_VISIBLE_DEVICES" not in a["env"] for a in victims_of(shared))
    assert all(a["ppn"] == 4 for a in apps_of(shared, "steady_a128k"))


def test_walltime_and_timeout_are_those_of_build_block(out: Path) -> None:
    for feature in FEATURES:
        opts = block_of(out, feature)["global_options"]
        assert opts["walltime"] == "00:30:00"  # the 30 min floor: the estimate is a few minutes
        assert opts["timeout"] == "600.0"


# --- CRAB's own checks, allocator and placement --------------------------------------------------


def _gpu_settings() -> Any:
    from crab.cli.presets import resolve_presets
    from crab.core.execution.settings import from_preset

    resolved = resolve_presets([], [(GPU_PRESET_FILE, json.loads(GPU_PRESET_FILE.read_text()))])
    body = dict(resolved["nplb_h100_gpu"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100_gpu")


def _shape(message: str) -> str:
    return re.sub(r"experiment \S+:", "experiment X:", message)


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


def test_every_block_validates_with_crab(out: Path) -> None:
    from crab.core.config_checks import check_config

    settings = _gpu_settings()
    committed: set[str] = set()
    for path in sorted(H100.glob("gpu_*.json")):
        committed.update(_shape(w) for w in check_config(json.loads(path.read_text()), settings))
    for feature in FEATURES:
        warnings = check_config(block_of(out, feature), settings)
        assert {_shape(w) for w in warnings} <= committed, (feature, warnings)


def test_every_block_places_through_the_real_allocator(out: Path) -> None:
    expected = {
        "split-i": (["n1", "n3"], ["n2", "n4"]),
        "split-l": (["n1", "n2"], ["n3", "n4"]),
        "shared": (NODES, NODES),
    }
    for feature, (layout, _, conditions) in FEATURES.items():
        victim_nodes, aggressor_nodes = expected[layout]
        config = block_of(out, feature)
        for exp in conditions:
            nodes = _nodes_of(config, exp)
            assert all(n == victim_nodes for n in nodes[:2]), (feature, exp)
            if exp != "alone":
                assert nodes[2] == aggressor_nodes, (feature, exp)


def test_every_wrapper_path_exists(out: Path) -> None:
    wrappers = NPLB.parents[1] / "wrappers"
    if not wrappers.exists():
        pytest.skip("no wrappers/ checkout")
    for feature in FEATURES:
        for app in victims_of(block_of(out, feature)) + aggressors_of(block_of(out, feature)):
            assert (wrappers / app["path"]).is_file(), (feature, app["path"])


# --- schedule.tsv --------------------------------------------------------------------------------


def test_schedule_rows_parse_with_run_log(out: Path) -> None:
    text = (out / "schedule.tsv").read_text()
    assert text.splitlines()[0] == HEADER and text.endswith("\n")
    rows = run_log.read_schedule(out / "schedule.tsv")
    assert [r.block for r in rows] == list(FEATURES)
    assert [r.n for r in rows] == [1, 2, 3, 4, 5]
    cum = 0.0
    for row, (feature, (layout, program, _)) in zip(rows, FEATURES.items(), strict=True):
        assert (row.tier, row.state, row.nplb, row.ar, row.sr) == ("S", "-", "-", "-", "-")
        assert row.layout == layout
        assert row.victim == ("ardc" if "allreduce" in program else "a2a")
        assert row.file == f"smoke_{feature}.json" and (out / row.file).is_file()
        assert row.est_min > 0
        assert (row.switch_change, row.sr_change) == (0, 0)
        assert row.cum_h >= cum
        cum = row.cum_h


def test_schedule_state_cells_match_the_local_rehearsal(tmp_path: Path) -> None:
    """The `-` state cells are the ones `campaign_plan.py --local` writes."""
    local = tmp_path / "local" / "laptop"
    result = subprocess.run(
        [sys.executable, str(NPLB / "campaign_plan.py"), "--out", str(local), "--local"],
        env=env_for(tmp_path, WORK=str(tmp_path / "work")),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reference = run_log.read_schedule(local / "schedule.tsv")[0]
    smoke_out = tmp_path / "local" / "smoke"
    assert run_smoke(tmp_path, "plan", "--out", str(smoke_out)).returncode == 0
    ours = run_log.read_schedule(smoke_out / "schedule.tsv")[0]
    assert (ours.state, ours.nplb, ours.ar, ours.sr) == (
        reference.state,
        reference.nplb,
        reference.ar,
        reference.sr,
    )


# --- the report ----------------------------------------------------------------------------------


def test_estimate_writes_nothing_and_reports_minutes(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "smoke"
    folder.mkdir(parents=True)
    before = snapshot(tmp_path)
    result = run_smoke(tmp_path, "plan", "--estimate", "--out", str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(tmp_path) == before and not list(folder.iterdir())
    lines = result.stdout.splitlines()
    assert lines[0].startswith("VERDICT: ")
    total = float(re.search(r"([\d.]+) min", lines[0]).group(1))  # type: ignore[union-attr]
    assert 5 <= total <= 20
    for feature in FEATURES:
        assert any(line.startswith(feature) for line in lines), feature
    assert "cost model" in result.stdout and "guess" in result.stdout
    assert "nothing written (--estimate)" in result.stdout
    assert "next: nplb smoke" not in result.stdout
    assert result.stderr == ""


def test_normal_run_reports_and_names_the_next_command(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "smoke"
    result = run_smoke(tmp_path, "plan", "--out", str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("VERDICT: OK")
    assert str(folder) in result.stdout
    assert lines[-1] == "next: nplb smoke"
    assert result.stderr == ""


def test_estimate_total_matches_the_schedule(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "smoke"
    full = run_smoke(tmp_path, "plan", "--out", str(folder))
    rows = run_log.read_schedule(folder / "schedule.tsv")
    total = float(re.search(r"([\d.]+) min", full.stdout.splitlines()[0]).group(1))  # type: ignore[union-attr]
    assert total == pytest.approx(sum(r.est_min for r in rows), abs=0.3)
    assert rows[-1].cum_h * 60 == pytest.approx(total, abs=0.5)


def test_features_command_lists_the_table(tmp_path: Path) -> None:
    result = run_smoke(tmp_path, "features")
    assert result.returncode == 0, result.stderr
    names = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    assert names == list(FEATURES)


# --- selection and the out folder ----------------------------------------------------------------


def test_feature_selection_keeps_table_order(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "smoke"
    result = run_smoke(tmp_path, "plan", "shared", "plain", "--out", str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in folder.iterdir()) == [
        "schedule.tsv",
        "smoke_plain.json",
        "smoke_shared.json",
    ]
    rows = run_log.read_schedule(folder / "schedule.tsv")
    assert [(r.n, r.block) for r in rows] == [(1, "plain"), (2, "shared")]


def test_unknown_feature_exits_2_and_writes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "smoke"
    result = run_smoke(tmp_path, "plan", "plain", "nope", "--out", str(folder))
    assert result.returncode == 2
    assert result.stdout == ""
    assert "nope" in result.stderr
    assert "plain, bursty, ardc, split-l, shared" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
    assert not folder.exists()


def test_out_outside_a_local_directory_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    result = run_smoke(tmp_path, "plan", "--out", str(folder))
    assert result.returncode == 2
    assert LOCAL_MESSAGE in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
    assert not folder.exists()


@pytest.mark.parametrize("variable", ["WORK", "NPLB_WORK"])
def test_default_out_is_under_the_work_folder(tmp_path: Path, variable: str) -> None:
    work = tmp_path / "somewhere" / "work"  # not under local/: the default is not refused
    result = run_smoke(tmp_path, "plan", **{variable: str(work)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "smoke" / "campaign" / "schedule.tsv").is_file()
    assert str(work / "smoke" / "campaign") in result.stdout


def test_default_work_folder_without_variables() -> None:
    smoke = _load("smoke")
    checkout = Path(__file__).resolve().parents[1]
    assert smoke.default_out({}) == checkout / "local" / "nplb-work" / "smoke" / "campaign"
    assert smoke.default_out({"NPLB_WORK": "/a"}) == Path("/a/smoke/campaign")
    assert smoke.default_out({"WORK": "/b", "NPLB_WORK": "/a"}) == Path("/b/smoke/campaign")


def test_output_is_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "local" / "a", tmp_path / "local" / "b"
    assert run_smoke(tmp_path, "plan", "--out", str(first)).returncode == 0
    assert run_smoke(tmp_path, "plan", "--out", str(second)).returncode == 0
    assert snapshot(first) == snapshot(second)
    assert run_smoke(tmp_path, "plan", "--out", str(first)).returncode == 0
    assert snapshot(first) == snapshot(second)


def test_python_310_safe(tmp_path: Path) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    folder = tmp_path / "local" / "smoke"
    result = run_smoke(tmp_path, "plan", "--out", str(folder), python=python310)
    assert result.returncode == 0, result.stdout + result.stderr


# --- campaign.build_block ------------------------------------------------------------------------


def test_build_block_without_iters_is_unchanged() -> None:
    assert campaign.build_block("split-i", "a2a") == campaign.build_block(
        "split-i", "a2a", iters=None
    )
    with_iters = campaign.build_block("split-i", "a2a", sizes=[65536], iters=(20, 2))[1]
    app = with_iters["experiments"]["alone"]["apps"]["0"]
    assert "-iter 20 -warmup 2 -grty 1 -maxsamples 20" in app["args"]
