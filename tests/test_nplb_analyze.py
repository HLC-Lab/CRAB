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
    data_dir: Path, exp: str, app: int, size: int, rows: list[tuple[Any, ...]], *, header_only=False
) -> None:
    """A data_app_<app>.csv with the columns CRAB writes for one collected app."""
    cols = [f"{app}_{c}-Duration_s" for c in ("Avg", "Min", "Max", "Median", "MainRank")]
    lines = [",".join(["run_id", "msg_size", *cols])]
    for run, mn, mx, med in [] if header_only else rows:
        lines.append(f"{run},{size},{med * US!r},{mn * US!r},{mx * US!r},{med * US!r},{mx * US!r}")
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
    assert (r["n_runs"], r["n_iters"], r["grty"]) == (2, 8, 1)
    assert r["t_med"] == pytest.approx(T_MED)
    assert r["t_p99"] == pytest.approx(T_P99)
    assert r["t_max_p99"] == pytest.approx(49.3 * US)
    assert r["bw_gbps"] == pytest.approx(SIZE * 8 / T_MED / 1e9)
    assert r["spread_med"] == pytest.approx(3.0)
    assert r["spread_p99"] == pytest.approx(4.93)
    assert r["spread_skipped"] == 0
    assert r["run_cv"] == pytest.approx(CV)
    assert r["slowdown_med"] == pytest.approx(1.0)  # an alone row against itself


def test_bandwidth_counts_the_granularity(
    tmp_path: Path, configs: dict[str, dict[str, Any]]
) -> None:
    config = copy.deepcopy(configs["smoke_md_p2_perm_C0_P1"])
    app = config["experiments"]["p2_perm_alone_C0_P1"]["apps"]["1"]
    app["args"] += " -grty 4"
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
    bw0 = SIZE * 8 / (35 * US * 2) / 1e9
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
