"""The campaign planner (examples/nplb/campaign_plan.py): tiers, the 8 network states, the block
files, `schedule.tsv`, the estimate report and the `nplb campaign` verb.

The planner is run as a subprocess (its CLI is the contract) and through its pure functions. Blocks
are put through CRAB's real `check_config`. Nothing starts a job or sends a signal; every file goes
under `tmp_path`.
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
PLAN_PY = NPLB / "campaign_plan.py"
GPU_PRESET_FILE = NPLB / "presets" / "nplb_h100_gpu.json"
HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)
LOCAL_MESSAGE = "--out must be inside a local/ directory"
VICTIMS = ["a2a", "agtr", "ardc"]


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


net_state = _load("net_state")
campaign_cost = _load("campaign_cost")
campaign = _load("campaign")
plan = _load("campaign_plan")


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "NPLB_NOW", "PAGE_LINES", "PAGE_COLS"):
        env.pop(var, None)
    env["WORK"] = str(tmp_path / "work")
    env["NPLB_REPORT_DIR"] = str(tmp_path / "reports")
    env.update(extra)
    return env


def run_plan(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PLAN_PY), *args],
        env=env_for(tmp_path, **env),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )


def written(tmp_path: Path, name: str = "camp", *args: str) -> tuple[Path, list[dict[str, str]]]:
    """Run the planner into <tmp>/local/<name>; (the folder, the schedule rows as dicts)."""
    out = tmp_path / "local" / name
    result = run_plan(tmp_path, "--out", str(out), *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return out, read_schedule(out)


def read_schedule(out: Path) -> list[dict[str, str]]:
    lines = (out / "schedule.tsv").read_text(encoding="utf-8").splitlines()
    names = lines[0].split("\t")
    return [dict(zip(names, line.split("\t"), strict=True)) for line in lines[1:]]


def table_rows(report: str) -> dict[str, list[str]]:
    """The tier table of a report: first word (tier or `total`) -> the other cells."""
    rows: dict[str, list[str]] = {}
    for line in report.splitlines():
        words = line.split()
        if words and (words[0] in ("1", "2", "3", "4", "L", "total")) and len(words) >= 8:
            rows[words[0]] = words[1:]
    return rows


def cells_of(out: Path, row: dict[str, str]) -> set[tuple[str, str, str, str, int]]:
    """(state, layout, victim, condition, size) of every measured cell of a schedule row."""
    config = json.loads((out / row["file"]).read_text())
    found = set()
    for condition, body in config["experiments"].items():
        for app in body["apps"].values():
            if "-iter" in app["args"]:  # the victim apps; the aggressor has no -iter
                words = app["args"].split()
                size = int(words[words.index("-msgsize") + 1])
                found.add((row["state"], row["layout"], row["victim"], condition, size))
    return found


# --- states, order, counting -------------------------------------------------------------------


def test_states_and_groups() -> None:
    labels = [net_state.label(*state) for state in plan.CANONICAL]
    assert len(labels) == 8 and len(set(labels)) == 8
    assert plan.GROUPS == ((0, 0), (1, 0), (0, 1), (1, 1))  # base, nplb, ar, nplb+ar
    assert [state[:2] for state in plan.CANONICAL] == [g for g in plan.GROUPS for _ in (0, 1)]
    assert all(label.startswith("nplb1_ar1_") for label in labels[-2:])
    assert labels[0] == "nplb0_ar0_sr0"


def test_change_counting_and_overheads() -> None:
    # 5 rows: switch groups base, base, nplb, nplb, ar -> first row, then 2 changes (3 in all);
    # sr values 0, 1, 1, 1, 0 -> first row, then 2 changes (3 in all).
    states = [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 1), (0, 1, 0)]
    jobs = [plan.Job("1", state, "t1", "split-i", "a2a") for state in states]
    costs = {
        ("t1", "split-i", "a2a"): plan.Cost(est_s=600.0, apps=2, victim_s=0.0, unmeasured_s=0.0)
    }
    rows = plan.build_rows(jobs, costs, switch_min=7.0, sr_min=3.0)
    assert [r.switch_change for r in rows] == [1, 0, 1, 0, 1]
    assert [r.sr_change for r in rows] == [1, 1, 0, 0, 1]
    assert [r.n for r in rows] == [1, 2, 3, 4, 5]
    # cum = run time + 7 min per switch change + 3 min per SR change, row by row
    assert rows[0].cum_s == 600 + 7 * 60 + 3 * 60
    assert rows[-1].cum_s == 5 * 600 + 3 * 7 * 60 + 3 * 3 * 60 == 4800
    assert plan.count_switch_changes([r.state for r in rows]) == 3
    # no state at all (the laptop mode): nothing to switch
    local = [plan.Job("L", None, "l", "split-i", "a2a")] * 2
    rows = plan.build_rows(local, {("l", "split-i", "a2a"): costs[("t1", "split-i", "a2a")]}, 10, 2)
    assert [(r.switch_change, r.sr_change) for r in rows] == [(0, 0), (0, 0)]
    assert rows[-1].cum_s == 1200


def test_greedy_state_order_starts_from_the_previous_state(tmp_path: Path) -> None:
    _, rows = written(tmp_path)
    by_tier: dict[str, list[str]] = {}
    for row in rows:
        states = by_tier.setdefault(row["tier"], [])
        if not states or states[-1] != row["state"]:
            states.append(row["state"])
    # Tier 1 from nothing: canonical first (000); then the cheapest step each time, ties by the
    # canonical order, the (1,1) states last: 000, 001 (SR only), 101 (1 switch change, SR kept,
    # beats 010 only by the canonical order), 100 (SR only), 010, 011, then 111, 110.
    assert by_tier["1"] == [
        "nplb0_ar0_sr0",
        "nplb0_ar0_sr1",
        "nplb1_ar0_sr1",
        "nplb1_ar0_sr0",
        "nplb0_ar1_sr0",
        "nplb0_ar1_sr1",
        "nplb1_ar1_sr1",
        "nplb1_ar1_sr0",
    ]
    # Tier 1 ends on nplb1_ar1_sr0. For tier 2 (KEY = 000 and 101) both candidates cost one switch
    # change; 000 keeps SR (0 -> 0), 101 changes it (0 -> 1), so tier 2 starts on 000.
    assert by_tier["2"] == ["nplb0_ar0_sr0", "nplb1_ar0_sr1"]
    # Tier 3 starts where tier 2 ended (101, no change at all), then 000.
    assert by_tier["3"] == ["nplb1_ar0_sr1", "nplb0_ar0_sr0"]
    # Tier 4 (the other six) from 000: 001 first (SR only), then 011, 010, 100, and (1,1) last.
    assert by_tier["4"] == [
        "nplb0_ar0_sr1",
        "nplb0_ar1_sr1",
        "nplb0_ar1_sr0",
        "nplb1_ar0_sr0",
        "nplb1_ar1_sr0",
        "nplb1_ar1_sr1",
    ]


# --- the schedule ------------------------------------------------------------------------------


def test_schedule_rows_tier_order(tmp_path: Path) -> None:
    _, rows = written(tmp_path)
    assert len(rows) == 24 + 12 + 12 + 36
    assert [r["tier"] for r in rows] == ["1"] * 24 + ["2"] * 12 + ["3"] * 12 + ["4"] * 36
    assert rows[0]["state"] == "nplb0_ar0_sr0"
    tier1 = [r for r in rows if r["tier"] == "1"]
    assert all(r["state"].startswith("nplb1_ar1_") for r in tier1[-6:])
    assert not any(r["state"].startswith("nplb1_ar1_") for r in tier1[:-6])
    tier4 = [r for r in rows if r["tier"] == "4"]
    assert {r["state"] for r in tier4} == {net_state.label(*s) for s in net_state.STATES} - {
        "nplb0_ar0_sr0",
        "nplb1_ar0_sr1",
    }
    assert all(r["state"].startswith("nplb1_ar1_") for r in tier4[-12:])
    # within one (tier, state): layout, then victim, then block part (t2a before t2b)
    layouts, victims, parts = ["split-i", "split-l", "shared"], VICTIMS, ["t1", "t2a", "t2b", "t3"]
    for tier in "1234":
        chunks: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            if r["tier"] == tier:
                chunks.setdefault(r["state"], []).append(r)
        assert len(chunks) == {"1": 8, "2": 2, "3": 2, "4": 6}[tier]
        for chunk in chunks.values():
            keys = [
                (layouts.index(r["layout"]), victims.index(r["victim"]), parts.index(r["block"]))
                for r in chunk
            ]
            assert keys == sorted(keys)
    assert {r["block"] for r in rows if r["tier"] == "2"} == {"t2a", "t2b"}
    assert [(r["block"], r["layout"]) for r in rows if r["tier"] == "3"][:6] == [
        ("t3", "split-l"),
        ("t3", "split-l"),
        ("t3", "split-l"),
        ("t3", "shared"),
        ("t3", "shared"),
        ("t3", "shared"),
    ]


def test_blocks_per_tier_and_no_cell_is_measured_twice(tmp_path: Path) -> None:
    out, rows = written(tmp_path)
    seen: set[tuple[str, str, str, str, int]] = set()
    per_tier: dict[str, int] = {}
    for row in rows:
        cells = cells_of(out, row)
        assert not (cells & seen), row
        seen |= cells
        per_tier[row["tier"]] = per_tier.get(row["tier"], 0) + len(cells)
    assert per_tier == {
        "1": 8 * 3 * 3 * 4,
        "2": 2 * 3 * (3 * 4 + 8 * 8),
        "3": 2 * 2 * 3 * 3 * 8,
        "4": 6 * 3 * (3 * 4 + 8 * 8),
    }
    assert per_tier == {"1": 288, "2": 456, "3": 288, "4": 1368}
    for state in ("nplb0_ar0_sr0", "nplb1_ar0_sr1"):  # a KEY state: all three layouts' worth
        mine = {c for c in seen if c[0] == state}
        assert sum(1 for c in mine if c[1] == "split-i") == 11 * 8 * 3 == 264
        assert sum(1 for c in mine if c[1] == "split-l") == 3 * 8 * 3 == 72
        assert sum(1 for c in mine if c[1] == "shared") == 72
    for state in {c[0] for c in seen} - {"nplb0_ar0_sr0", "nplb1_ar0_sr1"}:
        # any other state: the full split-i grid, made of tier 1 and tier 4
        mine = {c for c in seen if c[0] == state}
        assert len(mine) == 264 and {c[1] for c in mine} == {"split-i"}


def test_order_state_minimizes_switch_changes(tmp_path: Path) -> None:
    _, by_tier = written(tmp_path, "tier")
    _, by_state = written(tmp_path, "state", "--order", "state")
    assert sum(int(r["switch_change"]) for r in by_state) == 4  # set, then 3 group changes
    assert sum(int(r["switch_change"]) for r in by_tier) == 10
    assert len(by_state) == len(by_tier)
    states = [r["state"] for r in by_state]
    assert states[0] == "nplb0_ar0_sr0"
    state_seq = [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s]
    assert len(state_seq) == 8 and len(set(state_seq)) == 8  # one pass over the 8 states
    assert all(s.startswith("nplb1_ar1_") for s in state_seq[-2:])  # (1,1) last
    for state in state_seq:  # inside a state the tiers keep their order
        tiers = [r["tier"] for r in by_state if r["state"] == state]
        assert tiers == sorted(tiers)
    est = run_plan(tmp_path, "--estimate", "--order", "state").stdout
    assert re.search(r"order: state .*--order tier would need 10 switch changes instead of 4", est)
    est = run_plan(tmp_path, "--estimate").stdout
    assert re.search(r"order: tier .*--order state would need 4 switch changes instead of 10", est)
    assert "10 switch changes)" in est.splitlines()[0]


def test_schedule_tsv_shape(tmp_path: Path) -> None:
    out1, rows = written(tmp_path, "one")
    out2, _ = written(tmp_path, "two")
    text = (out1 / "schedule.tsv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == HEADER
    for line in text.splitlines():
        assert len(line.split("\t")) == 14
    assert [int(r["n"]) for r in rows] == list(range(1, len(rows) + 1))
    for r in rows:
        assert (out1 / r["file"]).is_file()
        assert r["file"] == f"{r['block']}_{r['layout']}_{r['victim']}.json"
        assert r["state"] == net_state.label(r["nplb"], r["ar"], r["sr"])
    assert (rows[0]["switch_change"], rows[0]["sr_change"]) == ("1", "1")
    cum = [float(r["cum_h"]) for r in rows]
    assert cum == sorted(cum) and cum[0] > 0
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", text)  # no timestamps
    names = sorted(p.name for p in out1.iterdir())
    assert names == sorted(p.name for p in out2.iterdir())
    assert len(names) == 16  # 15 blocks and the schedule
    for name in names:
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


# --- the blocks --------------------------------------------------------------------------------


def _gpu_settings() -> Any:
    from crab.cli.presets import resolve_presets
    from crab.core.execution.settings import from_preset

    resolved = resolve_presets([], [(GPU_PRESET_FILE, json.loads(GPU_PRESET_FILE.read_text()))])
    body = dict(resolved["nplb_h100_gpu"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100_gpu")


def _shape(message: str) -> str:
    return re.sub(r"experiment \S+:", "experiment X:", message)


def test_block_files_are_written_and_valid(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config

    out, _ = written(tmp_path)
    expected = sorted(
        [f"{b}_split-i_{v}.json" for b in ("t1", "t2a", "t2b") for v in VICTIMS]
        + [f"t3_{lay}_{v}.json" for lay in ("split-l", "shared") for v in VICTIMS]
    )
    assert sorted(p.name for p in out.glob("*.json")) == expected and len(expected) == 15
    settings = _gpu_settings()
    committed: set[str] = set()
    for path in sorted((NPLB / "h100").glob("gpu_*.json")):
        committed.update(_shape(w) for w in check_config(json.loads(path.read_text()), settings))
    for name in expected:
        config = json.loads((out / name).read_text())
        assert config["global_options"]["name"] == f"nplb_{name[: -len('.json')]}"
        assert {_shape(w) for w in check_config(config, settings)} <= committed, name
        if "_shared_" in name:
            assert config["global_options"]["allocation"] == {"mode": "shared"}
            texts = [e["description"] for e in config["experiments"].values()]
            assert any(
                "alongside, on separate GPUs, ports and cores of the same nodes" in t for t in texts
            )
            assert not any("aggressor nodes" in t for t in texts)
        else:
            assert "partitions" in config["global_options"]["allocation"]
    t1 = json.loads((out / "t1_split-i_a2a.json").read_text())
    assert list(t1["experiments"]) == ["alone", "steady_a128k", "burst_l10ms_p10ms_a128k"]
    t2b = json.loads((out / "t2b_split-i_agtr.json").read_text())
    assert len(t2b["experiments"]) == 8 and "alone" not in t2b["experiments"]


def test_estimate_matches_the_blocks_walltime(tmp_path: Path) -> None:
    out, rows = written(tmp_path)
    for row in rows[:40]:
        config = json.loads((out / row["file"]).read_text())
        h, m, s = (int(x) for x in config["global_options"]["walltime"].split(":"))
        walltime = h * 3600 + m * 60 + s
        est_s = float(row["est_min"]) * 60  # the table rounds to 0.1 min
        assert abs(walltime - max(1800, 1.5 * est_s)) <= 10, row["file"]


# --- the report --------------------------------------------------------------------------------


def test_estimate_report_shape(tmp_path: Path) -> None:
    result = run_plan(tmp_path, "--estimate")
    assert result.returncode == 0, result.stderr
    report = result.stdout
    lines = report.splitlines()
    assert not (tmp_path / "local").exists()
    assert lines[0].startswith("VERDICT: OK ") and "(84 runs, 10 switch changes)" in lines[0]
    assert "tiers" not in lines[0]  # no budget given
    assert len(lines) <= 34 and max(len(line) for line in lines) <= 100
    rows = table_rows(report)
    assert sorted(rows) == ["1", "2", "3", "4", "total"]
    assert any(
        line.split()[:3] == ["tier", "blocks", "runs"] and "unmeasured %" in line for line in lines
    )
    assert rows["1"][:2] == ["3", "24"]  # blocks, runs
    assert rows["total"][1] == "84"
    assert f"CONTENTION_FACTOR = {campaign_cost.CONTENTION_FACTOR}" in report
    assert f"ALLREDUCE_FACTOR = {campaign_cost.ALLREDUCE_FACTOR}" in report
    assert "ppn8" in report
    assert any(line.startswith("order: tier") for line in lines)
    assert "nothing written (--estimate)" in lines[-1]
    # the same report with the files written ends on the folder
    out = tmp_path / "local" / "camp"
    written_report = run_plan(tmp_path, "--out", str(out)).stdout.splitlines()
    assert written_report[-1] == f"files: {out}"
    # budgets, derived from the report's own cum h column (whole tiers only)
    cum = {tier: float(rows[tier][-2]) for tier in "1234"}
    assert cum["1"] < cum["2"] < cum["3"] < cum["4"]

    def verdict(budget: float) -> str:
        return run_plan(tmp_path, "--estimate", "--budget-hours", str(budget)).stdout.splitlines()[
            0
        ]

    assert "tiers 1-4 fit" in verdict(cum["4"] + 5)
    assert "tiers 1-2 fit" in verdict((cum["2"] + cum["3"]) / 2)
    assert "none fit" in verdict(0.1)
    assert verdict(0.1).startswith("VERDICT: OK ")


def test_unmeasured_share_is_reported(tmp_path: Path) -> None:
    # Measured: a2a/agtr victim points without an aggressor on split layouts (the `alone`
    # condition), except a2a at 256 MiB (extrapolated). Everything contended rests on the
    # contention factor, all ardc rows on the allreduce model, `shared` on the ppn8 proxy.
    sizes = [64 * 1024, 1 << 20, 16 << 20, 256 << 20]
    agtr = campaign_cost.block_estimate("agtr", "split-i", sizes, [False])
    assert set(agtr.victim_s) == {"measured"}
    a2a = campaign_cost.block_estimate("a2a", "split-i", sizes, [False])
    assert set(a2a.victim_s) == {"measured", "extrapolated"}
    ardc = campaign_cost.block_estimate("ardc", "split-i", sizes, [False])
    assert set(ardc.victim_s) == {"model-ardc"}
    both = campaign_cost.block_estimate("agtr", "shared", sizes, [False, True])
    assert set(both.victim_s) == {"proxy-ppn8", "proxy-ppn8+assumed-contention"}
    assert both.apps == 4 + 5 and both.seconds > 10 * both.apps
    rows = table_rows(run_plan(tmp_path, "--estimate").stdout)
    for tier, cells in rows.items():
        share = float(cells[-1])
        assert 0 < share < 100, (tier, share)


def test_node_map_lines(tmp_path: Path) -> None:
    without = run_plan(tmp_path, "--estimate").stdout
    assert "layouts: run nplb topo first to see the leaf classes" in without
    work = tmp_path / "work"
    work.mkdir()
    layouts = {
        "split-i": {"victim_pair": "same", "aggressor_pair": "same"},
        "split-l": {"victim_pair": "cross", "aggressor_pair": "partial"},
        "shared": {"victim_pair": "unknown", "aggressor_pair": "unknown"},
    }
    (work / "node_map.json").write_text(
        json.dumps({"when": "2026-11-10T09:30:00", "layouts": layouts})
    )
    report = run_plan(tmp_path, "--estimate").stdout
    assert "run nplb topo first" not in report
    assert "split-i victim pair same, aggressor pair same (map from 2026-11-10T09:30:00)" in report
    assert (
        "split-l victim pair cross, aggressor pair partial (map from 2026-11-10T09:30:00)" in report
    )
    assert "shared victim pair unknown, aggressor pair unknown (map from" in report
    assert len(report.splitlines()) <= 34


def test_out_must_be_inside_local(tmp_path: Path) -> None:
    for extra in ([], ["--estimate"]):
        result = run_plan(tmp_path, "--out", str(tmp_path / "plain"), *extra)
        assert result.returncode == 2
        assert LOCAL_MESSAGE in result.stderr
        assert result.stdout == ""
    assert not (tmp_path / "plain").exists()


def test_local_mode(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config
    from settings_fixtures import slurm_settings

    out, rows = written(tmp_path, "camp", "--local")
    assert len(rows) == 6
    assert {(r["tier"], r["state"], r["block"]) for r in rows} == {("L", "-", "l")}
    assert [(r["layout"], r["victim"]) for r in rows] == [
        (lay, v) for lay in ("split-i", "shared") for v in VICTIMS
    ]
    assert all(r["switch_change"] == "0" and r["sr_change"] == "0" for r in rows)
    assert all(r["nplb"] == r["ar"] == r["sr"] == "-" for r in rows)
    assert sorted(p.name for p in out.glob("*.json")) == sorted(r["file"] for r in rows)
    for row in rows:
        config = json.loads((out / row["file"]).read_text())
        assert config["global_options"]["name"] == f"nplb_l_{row['layout']}_{row['victim']}"
        assert "-gpu" not in json.dumps(config)
        assert check_config(config, slurm_settings()) == [], row["file"]
    report = run_plan(tmp_path, "--local", "--estimate").stdout
    assert "0 switch changes" in report.splitlines()[0]
    assert "(6 runs," in report.splitlines()[0]


# --- the dispatcher ----------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    # the planner imports campaign, gpu_set, make_configs, ... : copy every script of the folder
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


def run_nplb(tmp_path: Path, chk: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=env_for(tmp_path),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_dispatcher_campaign_verb(tmp_path: Path, checkout: Path) -> None:
    result = run_nplb(tmp_path, checkout, "campaign", "--estimate")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("campaign | page 1/")
    assert lines[1].startswith("VERDICT: OK ")
    assert len(lines) <= 38 and max(len(line) for line in lines) <= 100
    assert (tmp_path / "reports").is_dir() and any((tmp_path / "reports").iterdir())
    assert not (checkout / "local").exists()  # --estimate wrote nothing
    # narration stays on stderr as [HH:MM:SS] text
    assert re.search(r"^\[\d\d:\d\d:\d\d\] ", result.stderr, re.MULTILINE)

    bad = run_nplb(tmp_path, checkout, "campaign", "--out", str(tmp_path / "plain"))
    assert bad.returncode == 2
    assert LOCAL_MESSAGE in bad.stderr
    assert not (tmp_path / "plain").exists()

    helped = run_nplb(tmp_path, checkout, "help")
    help_lines = helped.stdout.splitlines()
    assert any(line.startswith("  campaign") for line in help_lines)
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100
