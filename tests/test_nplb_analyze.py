"""The NPLB campaign analysis (examples/nplb/analyze.py) on synthetic CRAB data directories."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"


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


make_configs = _load("make_configs")
analyze = _load("analyze")

US = 1e-6
# One CSV row is (run, min, max, median) in microseconds. Two runs of four iterations.
# Medians sorted: 10 20 20 30 40 40 60 80 -> median 35, p99 = 60 + 0.93 * 20 = 78.6.
# Run medians 25 and 50 -> CV = (25 / sqrt 2) / 37.5 = sqrt(2) / 3.
# Max / min per row: 2 2 3 3 2 4 4 5 -> median 3, p99 = 4 + 0.93 * 1 = 4.93.
# Max sorted: 20 20 20 30 30 40 40 50 -> p99 = 40 + 0.93 * 10 = 49.3.
ALONE = [
    (1, 10, 20, 10),
    (1, 10, 20, 20),
    (1, 10, 30, 30),
    (1, 10, 30, 40),
    (2, 10, 20, 20),
    (2, 10, 40, 40),
    (2, 10, 40, 60),
    (2, 10, 50, 80),
]
SIZE = 32768  # app "1" of every smoke chain
T_MED = 35 * US
T_P99 = 78.6 * US
CV = math.sqrt(2) / 3


def doubled(rows: list[tuple[int, int, int, int]], k: float = 2) -> list[tuple[Any, ...]]:
    """The same rows with the max and the median scaled by k (the min stays)."""
    return [(r, mn, mx * k, med * k) for r, mn, mx, med in rows]


def profile() -> dict[str, Any]:
    return {
        "nodes": ["n1", "n2", "n3", "n4"],
        "ppn": 4,
        "nics": [f"mlx5_{i}:1" for i in range(4)],
        "cpus": "0,16,32,48",
        "mem_gib": 1800,
    }


@pytest.fixture(scope="module")
def configs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """The smoke configs of the generator: {file stem: config}."""
    tmp = tmp_path_factory.mktemp("configs")
    (tmp / "profile.json").write_text(json.dumps(profile()))
    args = [str(tmp / "profile.json"), "--out", str(tmp / "out"), "--smoke", "--paths", "1"]
    assert make_configs.main([*args, "--net", "C0", "C2"]) == 0
    return {p.stem: json.loads(p.read_text()) for p in sorted((tmp / "out").glob("*.json"))}


def make_dir(root: Path, config: dict[str, Any], ts: str = "2026-01-01") -> Path:
    """A data dir named like CRAB's, holding the config."""
    path = root / f"{config['global_options']['name']}_{ts}"
    path.mkdir()
    (path / "config.json").write_text(json.dumps(config))
    return path


def write_csv(
    data_dir: Path,
    exp: str,
    app: int,
    size: int,
    rows: list[tuple[Any, ...]],
    *,
    header_only: bool = False,
    main_rank: bool = True,
) -> None:
    """A data_app_<app>.csv with the columns CRAB writes for one collected app.

    The MainRank column carries the row's max (it differs from the median); `main_rank=False`
    leaves the column out.
    """
    names = ["Avg", "Min", "Max", "Median"] + (["MainRank"] if main_rank else [])
    cols = [f"{app}_{c}-Duration_s" for c in names]
    lines = [",".join(["run_id", "msg_size", *cols])]
    for run, mn, mx, med in [] if header_only else rows:
        cells = [med * US, mn * US, mx * US, med * US, mx * US][: len(cols)]
        lines.append(",".join([str(run), str(size), *(repr(c) for c in cells)]))
    (data_dir / exp).mkdir(exist_ok=True)
    (data_dir / exp / f"data_app_{app}.csv").write_text("\n".join(lines) + "\n")


def summarize(
    tmp_path: Path, *dirs: Path, capsys: pytest.CaptureFixture[str] | None = None
) -> tuple[pd.DataFrame, str, str]:
    """Run the analysis; return the CSV, the markdown and what was printed."""
    out = tmp_path / "out"
    assert analyze.main([*map(str, dirs), "--out", str(out)]) == 0
    printed = capsys.readouterr().out if capsys else ""
    return pd.read_csv(out / "summary.csv"), (out / "summary.md").read_text(), printed


def row(df: pd.DataFrame, experiment: str, size: int = SIZE) -> pd.Series:
    found = df[(df["experiment"] == experiment) & (df["msg_size"] == size)]
    assert len(found) == 1, found
    return found.iloc[0]


def test_metrics_of_one_experiment(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    df, _, _ = summarize(tmp_path, data)
    r = row(df, "p2_perm_alone_C0_P1")
    assert r["data_dir"] == data.name and r["file"] == "smoke_md_p2_perm_C0_P1"
    assert (r["set"], r["phase"], r["victim"], r["net"], r["paths"]) == (
        "md",
        "p2",
        "perm",
        "C0",
        1,
    )
    assert r["smoke"] and r["app"] == 1 and r["program"] == "blink/pairwise_nb.py"
    assert r["aggressor"] != r["aggressor"]  # NaN: alone has none
    assert (r["n_runs"], r["n_iters"], r["grty"]) == (2, 8, 64)
    assert r["t_med"] == pytest.approx(T_MED)
    assert r["t_p99"] == pytest.approx(T_P99)
    assert r["t_max_p99"] == pytest.approx(49.3 * US)
    assert r["bw_gbps"] == pytest.approx(SIZE * 64 * 8 / T_MED / 1e9)
    assert r["spread_med"] == pytest.approx(3.0)
    assert r["spread_p99"] == pytest.approx(4.93)
    assert r["spread_skipped"] == 0
    assert r["run_cv"] == pytest.approx(CV)
    assert r["slowdown_med"] == pytest.approx(1.0)  # an alone row against itself


PINGPONG = "blink/pingpong_b.py"


def pingpong_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config with the victim app of the alone experiment swapped for a ping-pong."""
    config = copy.deepcopy(config)
    config["experiments"]["p2_perm_alone_C0_P1"]["apps"]["1"]["path"] = PINGPONG
    return config


# The ALONE rows' MainRank column is their max: 20 20 20 30 30 40 40 50 -> median 30, p99 = 49.3.
# Run medians (of the max) 25 and 40 -> CV = (15 / sqrt 2) / 32.5.
PP_T_MED = 30 * US
PP_CV = 15 / math.sqrt(2) / 32.5


def test_pingpong_time_comes_from_the_main_rank(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, pingpong_config(configs["smoke_md_p2_perm_C0_P1"]))
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    r = row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")
    assert r["program"] == PINGPONG
    assert r["t_med"] == pytest.approx(PP_T_MED)
    assert r["t_p99"] == pytest.approx(49.3 * US)
    assert r["t_max_p99"] == pytest.approx(49.3 * US)
    assert r["bw_gbps"] == pytest.approx(SIZE * 64 * 8 / PP_T_MED / 1e9)
    assert r["run_cv"] == pytest.approx(PP_CV)
    assert r["n_runs"] == 2 and r["n_iters"] == 8


def test_pingpong_has_no_spread(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, pingpong_config(configs["smoke_md_p2_perm_C0_P1"]))
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    df, md, _ = summarize(tmp_path, data)
    r = row(df, "p2_perm_alone_C0_P1")
    assert math.isnan(r["spread_med"]) and math.isnan(r["spread_p99"])
    assert r["spread_skipped"] == 0
    line = next(ln for ln in md.splitlines() if "| 32 KiB |" in ln)
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    assert cells[6] == "–" and cells[7] == "–"  # spread_med, spread_p99


def test_pingpong_without_main_rank_is_skipped(
    tmp_path: Path, configs: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    data = make_dir(tmp_path, pingpong_config(configs["smoke_md_p2_perm_C0_P1"]))
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE, main_rank=False)
    df, _, printed = summarize(tmp_path, data, capsys=capsys)
    assert df.empty
    assert "lacks columns: 1_MainRank-Duration_s" in printed


def test_other_apps_do_not_need_the_main_rank(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE, main_rank=False)
    r = row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")
    assert r["t_med"] == pytest.approx(T_MED) and r["spread_med"] == pytest.approx(3.0)


def test_bandwidth_counts_the_granularity(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    config = copy.deepcopy(configs["smoke_md_p2_perm_C0_P1"])
    app = config["experiments"]["p2_perm_alone_C0_P1"]["apps"]["1"]
    app["args"] = app["args"].replace("-grty 64", "-grty 4")
    data = make_dir(tmp_path, config)
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    df, _, _ = summarize(tmp_path, data)
    r = row(df, "p2_perm_alone_C0_P1")
    assert r["grty"] == 4
    assert r["bw_gbps"] == pytest.approx(SIZE * 4 * 8 / T_MED / 1e9)


def test_one_run_has_no_cv(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, [r for r in ALONE if r[0] == 1])
    df, _, _ = summarize(tmp_path, data)
    r = row(df, "p2_perm_alone_C0_P1")
    assert r["n_runs"] == 1 and math.isnan(r["run_cv"])
    assert r["t_med"] == pytest.approx(25 * US)


def test_rows_with_a_zero_min_are_left_out_of_the_spread(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    rows = [(1, 0, 20, 10), *ALONE[1:]]
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, rows)
    r = row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")
    assert r["spread_skipped"] == 1
    assert r["spread_med"] == pytest.approx(3.0)  # 2 3 3 2 4 4 5 -> 3


def test_slowdown_against_the_alone_of_the_same_file(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    write_csv(data, "p2_perm_vs_a2a_C0_P1", 1, SIZE, doubled(ALONE, 2))
    write_csv(data, "p2_perm_vs_incast_C0_P1", 1, SIZE, doubled(ALONE, 3))
    df, _, _ = summarize(tmp_path, data)
    a2a = row(df, "p2_perm_vs_a2a_C0_P1")
    assert a2a["aggressor"] == "a2a"
    assert a2a["slowdown_med"] == pytest.approx(2.0)
    assert a2a["slowdown_p99"] == pytest.approx(2.0)
    incast = row(df, "p2_perm_vs_incast_C0_P1")
    assert incast["slowdown_med"] == pytest.approx(3.0)
    assert incast["slowdown_p99"] == pytest.approx(3.0)


def test_slowdown_needs_the_alone_of_the_same_size(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 0, 8, ALONE)  # another size
    write_csv(data, "p2_perm_vs_a2a_C0_P1", 1, SIZE, doubled(ALONE))
    write_csv(data, "p2_perm_vs_a2a_C0_P1", 0, 8, doubled(ALONE))
    df, _, _ = summarize(tmp_path, data)
    assert row(df, "p2_perm_vs_a2a_C0_P1", 8)["slowdown_med"] == pytest.approx(2.0)
    assert math.isnan(row(df, "p2_perm_vs_a2a_C0_P1")["slowdown_med"])


def test_colleague_set_has_no_baseline(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, configs["smoke_col_a2a_C0_P1"])
    exp = "cong_burst-0.01-0.0001_C0_P1"
    write_csv(data, exp, 0, 8, ALONE)
    df, md, _ = summarize(tmp_path, data)
    r = row(df, exp, 8)
    assert (r["set"], r["phase"], r["victim"]) == ("col", "cong", "a2a")
    assert r["aggressor"] == "burst-0.01-0.0001"
    assert r["program"] == "blink/alltoall_comm_only.py"
    assert math.isnan(r["slowdown_med"]) and math.isnan(r["slowdown_p99"])
    assert "no alone baseline in this set" in md


def test_c0_against_c2_pairing(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    c0 = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"], "2026-01-01")
    c2 = make_dir(tmp_path, configs["smoke_md_p2_perm_C2_P1"], "2026-01-02")
    for data, net, k in ((c0, "C0", 1), (c2, "C2", 0.5)):
        write_csv(data, f"p2_perm_alone_{net}_P1", 1, SIZE, doubled(ALONE, k))
        write_csv(data, f"p2_perm_vs_a2a_{net}_P1", 1, SIZE, doubled(ALONE, 2 * k))
    write_csv(c0, "p2_perm_vs_a2a_C0_P1", 0, 8, ALONE)  # a size C2 does not have
    _, md, _ = summarize(tmp_path, c0, c2)
    sections = md.split("### C0 vs C2")[1:]
    assert len(sections) == 2  # the alone and the vs_a2a victim tests
    section = next(s for s in sections if "perm_vs_a2a" in s.splitlines()[0])
    cells = {}
    for line in section.splitlines():
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        cells[parts[0]] = parts
    header = cells["size"]
    bw0 = SIZE * 64 * 8 / (35 * US * 2) / 1e9
    got = dict(zip(header, cells["32 KiB"], strict=True))
    assert got["bw C0"] == f"{bw0:.3g}"
    assert got["bw C2"] == f"{bw0 * 2:.3g}"
    assert got["bw C2/C0"] == "2.00"
    assert got["slowdown C0"] == "2.00" and got["slowdown C2"] == "2.00"
    assert got["cv C0"] == f"{CV:.3f}" == got["cv C2"]
    only_c0 = dict(zip(header, cells["8 B"], strict=True))
    assert only_c0["bw C2"] == "–" and only_c0["bw C2/C0"] == "–"


def test_main_tables_use_human_sizes(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    for app, size in enumerate((8, SIZE, 2 * 1024 * 1024)):
        write_csv(data, "p2_perm_alone_C0_P1", app, size, ALONE)
    _, md, _ = summarize(tmp_path, data)
    assert "| 8 B |" in md and "| 32 KiB |" in md and "| 2 MiB |" in md
    assert "bw_gbps" in md and "slowdown_p99" in md
    assert "one-way" in md


def test_problems_are_reported_not_fatal(
    tmp_path: Path, configs: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    config = copy.deepcopy(configs["smoke_md_p2_perm_C0_P1"])
    config["experiments"]["weird_name"] = copy.deepcopy(
        config["experiments"]["p2_perm_alone_C0_P1"]
    )
    data = make_dir(tmp_path, config)
    write_csv(data, "weird_name", 1, SIZE, ALONE)
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)  # the good one
    write_csv(data, "p2_perm_alone_C0_P1", 0, 8, ALONE, header_only=True)  # empty
    # app 2 of the alone and every app of the other experiments: no CSV at all
    df, _, printed = summarize(tmp_path, data, capsys=capsys)
    assert set(df["experiment"]) == {"p2_perm_alone_C0_P1"}
    assert list(df["msg_size"]) == [SIZE]
    assert "weird_name" in printed and "unknown" in printed
    assert "empty" in printed and "p2_perm_alone_C0_P1" in printed
    assert "missing" in printed and "p2_perm_vs_a2a_C0_P1" in printed
    assert "1 data dir" in printed


def test_dir_without_config_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "nothing_here"
    empty.mkdir()
    df, md, printed = summarize(tmp_path, empty, capsys=capsys)
    assert df.empty
    assert "config.json" in printed and "nothing_here" in printed
    assert md.strip()


def test_writes_only_inside_out(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    data = make_dir(inputs, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)

    def snapshot() -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in inputs.rglob("*") if p.is_file()}

    before = snapshot()
    out = tmp_path / "deep" / "out"
    assert analyze.main([str(data), "--out", str(out)]) == 0
    assert snapshot() == before
    assert {p.name for p in out.iterdir()} == {"summary.csv", "summary.md"}
    assert {p.name for p in tmp_path.iterdir()} == {"inputs", "deep"}


# --- The ping-pong set: per-run statistics and the Ceilings section -------------------------------

PP_SIZES = (8, 2 * 1024 * 1024)
# Times in microseconds, one list per run; the ping-pong time is the MainRank column (the CSV
# helper writes a row's `max` there). Run medians 10, 25, 42.5 -> median of them 25 (the pooled
# median of all ten iterations is 20.5, so the two differ). Slow iterations (over 1.5 x the median
# of their own run: 15, 37.5, 63.75): 16 and 31 of run 1 -> 2 of 10 = 20 %. Against the pooled
# median (30.75) 31, 40 and 45 would count: 30 %.
PP_RUNS = {1: [10, 10, 10, 10, 16, 31], 2: [25, 25], 3: [40, 45]}
PP_T_RUN = (10 * US, 25 * US, 42.5 * US)  # fastest, median and slowest run median
PP_GRTY = 2
# The node total (pairwise, Max and Median columns): Median 5 / 8 / 9 -> run medians 5, 8, 9;
# Max 10 10 / 12 12 / 30 30 -> run medians 10, 12, 30: only the Max of run 3 is not slow, and
# each run's own median says that none is (15, 18, 45).
PW_RUNS = {1: [(1, 10, 5), (1, 10, 5)], 2: [(1, 12, 8), (1, 12, 8)], 3: [(1, 30, 9), (1, 30, 9)]}


def pp_app(path: str, size: int, **extra: Any) -> dict[str, Any]:
    args = f"-msgsize {size} -iter 100 -grty {PP_GRTY}"
    return {"path": path, "args": args, "collect": True, "start": "0", "end": "", **extra}


def pp_config(
    stem: str,
    tests: tuple[str, ...],
    global_ppn: str = "1",
    sizes: tuple[int, ...] = PP_SIZES,
    **app: Any,
) -> dict[str, Any]:
    """A config of the ping-pong set: one experiment per test, one collected app per size."""
    experiments = {}
    for test in tests:
        path = "blink/pairwise_nb.py" if test.endswith("pw8") else PINGPONG
        apps = {str(i): pp_app(path, size, **app) for i, size in enumerate(sizes)}
        experiments[f"pp_{test}_C2_P1"] = {"apps": apps}
    return {"global_options": {"name": stem, "ppn": global_ppn}, "experiments": experiments}


def write_pp(data: Path, test: str, sizes: tuple[int, ...] = PP_SIZES) -> None:
    """The CSVs of one pp experiment (every size with the same rows)."""
    for app, size in enumerate(sizes):
        if test.endswith("pw8"):
            rows = [(r, 1, mx, med) for r, its in PW_RUNS.items() for _, mx, med in its]
        else:
            rows = [(r, 1, t, 1) for r, its in PP_RUNS.items() for t in its]
        write_csv(data, f"pp_{test}_C2_P1", app, size, rows)


@pytest.fixture
def pp_dirs(tmp_path: Path) -> list[Path]:
    pair = make_dir(tmp_path, pp_config("pp_n1n2_C2_P1", ("n1n2-md", "n1n2-default", "n1n2-dual")))
    for test in ("n1n2-md", "n1n2-default", "n1n2-dual"):
        write_pp(pair, test)
    total = make_dir(tmp_path, pp_config("pp_n1n3_pw8_C2_P1", ("n1n3-pw8",), global_ppn="4"))
    write_pp(total, "n1n3-pw8")
    return [pair, total]


def test_pp_rows_carry_phase_victim_and_one_way_time(pp_dirs: list[Path], tmp_path: Path) -> None:
    df, _, _ = summarize(tmp_path, *pp_dirs)
    assert set(df["phase"]) == {"pp"}
    assert set(df["test"]) == {"n1n2-md", "n1n2-default", "n1n2-dual", "n1n3-pw8"}
    r = row(df, "pp_n1n2-md_C2_P1", 8)
    assert r["victim"] == r["test"] == "n1n2-md"
    assert r["n_runs"] == 3 and r["n_iters"] == 10 and r["grty"] == PP_GRTY
    assert r["t_med"] == pytest.approx(20.5 * US)  # the pooled median stays as before
    assert r["bw_gbps"] == pytest.approx(8 * PP_GRTY * 8 / (20.5 * US) / 1e9)


def test_bandwidth_from_the_per_run_medians(pp_dirs: list[Path], tmp_path: Path) -> None:
    df, _, _ = summarize(tmp_path, *pp_dirs)
    r = row(df, "pp_n1n2-md_C2_P1", 2 * 1024 * 1024)
    bits = 2 * 1024 * 1024 * PP_GRTY * 8 / 1e9
    fast, mid, slow = PP_T_RUN
    assert r["bw_runs_med"] == pytest.approx(bits / mid)
    assert r["bw_runs_max"] == pytest.approx(bits / fast)  # the fastest run
    assert r["bw_runs_min"] == pytest.approx(bits / slow)  # the slowest run
    assert r["bw_runs_med"] != pytest.approx(r["bw_gbps"])


def test_slow_iterations_are_counted_against_their_own_run(
    pp_dirs: list[Path], tmp_path: Path
) -> None:
    df, _, _ = summarize(tmp_path, *pp_dirs)
    assert row(df, "pp_n1n2-md_C2_P1", 8)["slow_pct"] == pytest.approx(20.0)
    # The node total is no ping-pong: the Max column against the median of the Max column.
    assert row(df, "pp_n1n3-pw8_C2_P1", 8)["slow_pct"] == pytest.approx(0.0)


def test_slow_iterations_of_a_non_pingpong_use_the_max_column(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    r = row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")
    # Max per run: 20 20 30 30 (median 25) and 20 40 40 50 (median 40): over 37.5 and 60: none.
    assert r["slow_pct"] == pytest.approx(0.0)
    rows = [(1, 10, 20, 10)] * 3 + [(1, 10, 40, 10)] + [(2, 10, 20, 10)] * 4
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, rows)
    # Run 1 Max: 20 20 20 40 -> median 20, 40 > 30 is slow: 1 of 8.
    assert row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")["slow_pct"] == pytest.approx(
        12.5
    )


def test_node_total_scales_the_per_rank_bandwidth(pp_dirs: list[Path], tmp_path: Path) -> None:
    df, _, _ = summarize(tmp_path, *pp_dirs)
    total = row(df, "pp_n1n3-pw8_C2_P1", 8)
    assert total["program"] == "blink/pairwise_nb.py"
    # Median column run medians 5 8 9 -> 8 us; the config's ppn is 4.
    assert total["bw_runs_med"] == pytest.approx(8 * PP_GRTY * 8 / (8 * US) / 1e9)
    assert total["node_gbps"] == pytest.approx(4 * total["bw_runs_med"])
    assert math.isnan(row(df, "pp_n1n2-md_C2_P1", 8)["node_gbps"])


def test_node_total_prefers_the_ppn_of_the_app(tmp_path: Path) -> None:
    data = make_dir(tmp_path, pp_config("pp_n1n3_pw8_C2_P1", ("n1n3-pw8",), global_ppn="4", ppn=8))
    write_pp(data, "n1n3-pw8")
    df, _, _ = summarize(tmp_path, data)
    r = row(df, "pp_n1n3-pw8_C2_P1", 8)
    assert r["node_gbps"] == pytest.approx(8 * r["bw_runs_med"])


def test_other_rows_have_the_per_run_columns_too(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    r = row(summarize(tmp_path, data)[0], "p2_perm_alone_C0_P1")
    # Run medians 25 and 50: the median of them is 37.5 (the pooled one is 35).
    assert r["bw_runs_med"] == pytest.approx(SIZE * 64 * 8 / (37.5 * US) / 1e9)
    assert r["bw_runs_max"] == pytest.approx(SIZE * 64 * 8 / (25 * US) / 1e9)
    assert r["bw_runs_min"] == pytest.approx(SIZE * 64 * 8 / (50 * US) / 1e9)
    assert r["bw_gbps"] == pytest.approx(SIZE * 64 * 8 / T_MED / 1e9)  # unchanged


def section(md: str) -> list[str]:
    """The lines of the Ceilings section of summary.md."""
    lines = md.splitlines()
    start = lines.index("# Ceilings (ping-pong set)")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return lines[start:end]


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_ceilings_come_first_after_the_intro(pp_dirs: list[Path], tmp_path: Path) -> None:
    _, md, _ = summarize(tmp_path, *pp_dirs)
    lines = md.splitlines()
    assert lines[0] == "# NPLB campaign summary"
    headings = [ln for ln in lines if ln.startswith("#")]
    assert headings[:2] == ["# NPLB campaign summary", "# Ceilings (ping-pong set)"]
    assert md.index("# Ceilings") > md.index("- bw_gbps:")
    assert " pp n1n" not in md  # no per-phase table of the pp rows: no second copy


def test_ceilings_table_of_bandwidth(pp_dirs: list[Path], tmp_path: Path) -> None:
    _, md, _ = summarize(tmp_path, *pp_dirs)
    lines = section(md)
    tables = [i for i, ln in enumerate(lines) if ln.startswith("size")]
    assert len(tables) == 2
    header = cells(lines[tables[0]])
    assert header == ["size", "n1n2 md", "n1n2 def", "n1n2 dual", "n1n3 pw8/rank", "n1n3 pw8/node"]
    body = {cells(ln)[0]: cells(ln) for ln in lines[tables[0] + 2 : tables[0] + 4]}
    assert set(body) == {"8 B", "2 MiB"}
    big = 2 * 1024 * 1024 * PP_GRTY * 8 / (25 * US) / 1e9
    got = dict(zip(header, body["2 MiB"], strict=True))
    # 1342.2 Gb/s per rank on the pair, 4194.3 per rank and 16777.2 per node on the total:
    # from 100 up the cells are whole numbers.
    assert big == pytest.approx(1342.177)
    assert got["n1n2 md"] == got["n1n2 def"] == got["n1n2 dual"] == "1342"
    assert got["n1n3 pw8/rank"] == "4194" and got["n1n3 pw8/node"] == "16777"
    small = dict(zip(header, body["8 B"], strict=True))
    assert small["n1n2 md"] == "0.00512" and small["n1n3 pw8/rank"] == "0.016"  # 3 significant
    text = "\n".join(lines)
    assert "runs: 3" in text and "medians of per-run medians" in text and "one-way" in text


def test_ceilings_table_of_slow_iterations(pp_dirs: list[Path], tmp_path: Path) -> None:
    _, md, _ = summarize(tmp_path, *pp_dirs)
    lines = section(md)
    second = [i for i, ln in enumerate(lines) if ln.startswith("size")][1]
    header = cells(lines[second])
    got = dict(zip(header, cells(lines[second + 2]), strict=True))
    assert got["n1n2 md"] == got["n1n2 dual"] == "20.0"
    assert got["n1n3 pw8/rank"] == "0.0"


def test_ceilings_stay_compact_for_the_full_set(tmp_path: Path) -> None:
    kib, mib = 1024, 1024 * 1024
    sizes = (8, 64, 512, 4 * kib, 32 * kib, 64 * kib, 128 * kib, 256 * kib, 512 * kib, mib, 2 * mib)
    tests = tuple(f"n1n{j}-{v}" for j in (2, 3, 4) for v in ("md", "default", "dual"))
    tests += ("n1n3-pw8",)
    data = make_dir(tmp_path, pp_config("pp_n1n2_C2_P1", tests, global_ppn="8", sizes=sizes))
    for test in tests:
        write_pp(data, test, sizes)
    _, md, _ = summarize(tmp_path, data)
    lines = section(md)
    assert len(lines) <= 40, len(lines)
    assert max(len(ln) for ln in lines) <= 120, max(lines, key=len)
    assert cells(next(ln for ln in lines if "size" in ln))[1:] == [
        *(f"n1n{j} {v}" for j in (2, 3, 4) for v in ("md", "def", "dual")),
        "n1n3 pw8/rank",
        "n1n3 pw8/node",
    ]


def test_missing_variant_leaves_its_column_out(tmp_path: Path) -> None:
    data = make_dir(tmp_path, pp_config("pp_n1n2_C2_P1", ("n1n2-md", "n1n2-default")))
    for test in ("n1n2-md", "n1n2-default"):
        write_pp(data, test)
    _, md, _ = summarize(tmp_path, data)
    header = next(cells(ln) for ln in section(md) if "size" in ln)
    assert header == ["size", "n1n2 md", "n1n2 def"]


def test_unreadable_pp_csv_is_reported(
    pp_dirs: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (pp_dirs[0] / "pp_n1n2-dual_C2_P1" / "data_app_0.csv").write_bytes(b"\xff\xfe\x00 \x80\n\x00")
    df, md, printed = summarize(tmp_path, *pp_dirs, capsys=capsys)
    assert "pp_n1n2-dual_C2_P1" in printed and "CSV" in printed
    assert 8 not in set(df[df["test"] == "n1n2-dual"]["msg_size"])
    assert "n1n2 md" in md


def test_no_ceilings_without_pp_rows(tmp_path: Path, configs: dict[str, dict[str, Any]]) -> None:
    data = make_dir(tmp_path, configs["smoke_md_p2_perm_C0_P1"])
    write_csv(data, "p2_perm_alone_C0_P1", 1, SIZE, ALONE)
    _, md, _ = summarize(tmp_path, data)
    assert "Ceilings" not in md and "## md (smoke) p2 perm" in md


def test_runs_per_column_become_a_row_when_they_differ(tmp_path: Path) -> None:
    data = make_dir(tmp_path, pp_config("pp_n1n2_C2_P1", ("n1n2-md", "n1n2-default")))
    write_pp(data, "n1n2-md")
    for app, size in enumerate(PP_SIZES):  # the default variant has only the first two runs
        rows = [(r, 1, t, 1) for r, its in PP_RUNS.items() if r < 3 for t in its]
        write_csv(data, "pp_n1n2-default_C2_P1", app, size, rows)
    _, md, _ = summarize(tmp_path, data)
    lines = section(md)
    assert "runs: see the last row" in "\n".join(lines)
    last = next(cells(ln) for ln in lines if ln.startswith("runs "))
    assert last == ["runs", "3", "2"]  # n1n2 md, n1n2 def


@pytest.mark.parametrize(
    ("value", "cell_text"),
    [
        (1040.4, "1040"),
        (168.4, "168"),
        (99.96, "100"),
        (41.94, "41.9"),
        (0.0409, "0.0409"),
        (float("nan"), "–"),
    ],
)
def test_ceilings_bandwidth_cells(value: float, cell_text: str) -> None:
    assert analyze.ceiling_bw(value) == cell_text
