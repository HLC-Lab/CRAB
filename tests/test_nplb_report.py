"""The lab analysis (examples/nplb/report.py, report_data.py, report_tables.py): `nplb report`, `nplb compare`.

Run folders are built in `tmp_path` in the shape `nplb run` leaves (config.json, nplb_state.json,
schedule_row.json, `<experiment>/data_app_<k>.csv`) and, for the real shape, from two small excerpts of
the October 2026 data (tests/fixtures/nplb/*_ppn8_apps.csv). The verbs are driven through `report.main`
and through the dispatcher in a fake checkout. Nothing starts a job or sends a signal; no network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
FIX = Path(__file__).resolve().parent / "fixtures" / "nplb"
REPORT_PY = NPLB / "report.py"
RUNS_HEADER = "n\tstate\tblock\tlayout\tvictim\tjob\tdata_dir\tsubmitted\tstatus\telapsed_s\tnote"
NOW = "2026-11-10T12:00:00"
S0, S1, S2 = "nplb0_ar0_sr0", "nplb1_ar0_sr1", "nplb0_ar1_sr0"
OCTOBER_SIZES = [1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864]
OCTOBER_SIZES += [268435456, 536870912]
KIB = 1024


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


campaign = _load("campaign")
pages = _load("pages")
report_data = _load("report_data")
report_tables = _load("report_tables")
report = _load("report")


# --- builders ------------------------------------------------------------------------------------


def csv_text(k: int, size: int, values: list[float]) -> str:
    """A data_app_<k>.csv in the engine's header, with `values` in the Max column (the slowest rank)."""
    head = f"run_id,msg_size,{k}_Avg-Duration_s,{k}_Min-Duration_s,{k}_Max-Duration_s"
    head += f",{k}_Median-Duration_s,{k}_MainRank-Duration_s"
    rows = [f"1,{size},{v * 0.5!r},{v * 0.4!r},{v!r},{v * 0.6!r},{v * 0.55!r}" for v in values]
    return "\n".join([head, *rows]) + "\n"


def make_run(
    root: Path,
    name: str,
    conds: dict[str, dict[int, list[float] | None]],
    *,
    state: str | None = S0,
    layout: str | None = "split-i",
    victim: str | None = "a2a",
    n: int | None = None,
) -> Path:
    """A campaign-style run folder. `conds`: condition -> {size: samples, or None for a missing csv}."""
    folder = root / name
    folder.mkdir(parents=True)
    experiments = {}
    for cond, by_size in conds.items():
        apps: dict[str, dict[str, object]] = {}
        for k, (size, samples) in enumerate(by_size.items()):
            args = f"-msgsize {size} -iter 5 -warmup 1 -grty 1 -gpu"
            apps[str(k)] = {"args": args, "collect": True, "ppn": 8}
            if samples is not None:
                (folder / cond).mkdir(exist_ok=True)
                (folder / cond / f"data_app_{k}.csv").write_text(csv_text(k, size, samples))
        if cond != "alone":
            apps[str(len(apps))] = {"args": "-msgsize 131072 -endl -warmup 0", "collect": False}
        experiments[cond] = {"description": cond, "apps": apps}
    config = {"schema_version": 1, "global_options": {}, "experiments": experiments}
    (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if state is not None:
        bits = re.findall(r"\d", state)
        record = {
            "switch": {"nplb": "on" if bits[0] == "1" else "off", "ar": "off"},
            "nic": {"sr": int(bits[2]), "ar": 0},
            "label": state,
            "crab_rev": None,
            "blink_rev": None,
        }
        (folder / "nplb_state.json").write_text(json.dumps(record), encoding="utf-8")
    if n is not None:
        row = {"n": n, "tier": "1", "state": state, "block": "t1", "layout": layout}
        row["victim"] = victim
        (folder / "schedule_row.json").write_text(json.dumps(row), encoding="utf-8")
    return folder


def log_line(n: int, folder: Path | str, state: str = S0, **fields: str) -> str:
    cells = {
        "n": str(n),
        "state": state,
        "block": "t1",
        "layout": "split-i",
        "victim": "a2a",
        "job": str(1000 + n),
        "data_dir": str(folder),
        "submitted": f"2026-11-10T10:{n:02d}:00",
        "status": "OK",
        "elapsed_s": "300",
        "note": "",
    }
    cells.update(fields)
    return "\t".join(cells[c] for c in RUNS_HEADER.split("\t"))


def write_runs_log(work: Path, lines: list[str]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    (work / "campaign_runs.tsv").write_text(
        "\n".join([RUNS_HEADER, *lines]) + "\n", encoding="utf-8"
    )


class Lab:
    """A temp WORK, data and report folder, and a way to run the verbs in process."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        self.work = tmp_path / "work"
        self.data = tmp_path / "data"
        self.data.mkdir()
        for var in ("NPLB_WORK", "PAGE_LINES", "PAGE_COLS", "PYTHONPATH"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("WORK", str(self.work))
        monkeypatch.setenv("DATA_ROOT", str(self.data))
        monkeypatch.setenv("NPLB_REPORT_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("NPLB_NOW", NOW)
        self.capsys: pytest.CaptureFixture[str] | None = None

    def run_dir(self, name: str, conds: dict, **kw: object) -> Path:
        return make_run(self.data, name, conds, **kw)  # type: ignore[arg-type]

    def log(self, lines: list[str]) -> None:
        write_runs_log(self.work, lines)

    def call(self, *args: str) -> tuple[int, list[str], str]:
        assert self.capsys is not None
        code = report.main(list(args))
        captured = self.capsys.readouterr()
        return code, captured.out.split("\n"), captured.err


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Lab:
    made = Lab(tmp_path, monkeypatch)
    made.capsys = capsys
    return made


def first_page(lines: list[str]) -> list[str]:
    """The lines before the first form feed (page 1), no trailing empty line."""
    page = lines[: lines.index("\f")] if "\f" in lines else list(lines)
    while page and page[-1] == "":
        page.pop()
    return page


def titles(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("== ")]


def table_after(lines: list[str], title_start: str) -> list[list[str]]:
    """The tokens of every line of the table below the title that starts with `title_start`."""
    index = next(i for i, line in enumerate(lines) if line.startswith(title_start))
    rows = []
    for line in lines[index + 1 :]:
        if line in ("", "\f") or line.startswith("== "):
            break
        rows.append(line.split())
    return rows


def fixture_blocks(name: str) -> dict[int, tuple[int, str]]:
    """k -> (message size, the CSV text) of an excerpt fixture."""
    blocks: dict[int, tuple[int, list[str]]] = {}
    current = -1
    for line in (FIX / name).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"### app (\d+) msgsize (\d+)", line)
        if match:
            current = int(match.group(1))
            blocks[current] = (int(match.group(2)), [])
        elif line.startswith("#"):
            continue
        else:
            blocks[current][1].append(line)
    return {k: (size, "\n".join(rows) + "\n") for k, (size, rows) in blocks.items()}


def expected_stats(csv: str) -> tuple[int, float, float]:
    """(n, median, p99) of the Max-Duration column, computed here independently of the module."""
    rows = [line.split(",") for line in csv.splitlines()]
    column = next(i for i, name in enumerate(rows[0]) if name.endswith("_Max-Duration_s"))
    values = sorted(float(row[column]) for row in rows[1:])
    rank = -(-99 * len(values) // 100)  # nearest rank: ceil(0.99 n)
    return len(values), statistics.median(values), values[rank - 1]


def october_run(root: Path, name: str, fixture: str) -> Path:
    """A bare run folder (no state or schedule files) with the apps of a real excerpt, experiment alone."""
    folder = root / name
    (folder / "alone").mkdir(parents=True)
    apps = {}
    for k, size in enumerate(OCTOBER_SIZES):
        apps[str(k)] = {"args": f"-msgsize {size} -iter 1000 -warmup 100 -grty 1 -gpu"}
        apps[str(k)]["collect"] = True
    config = {"experiments": {"alone": {"apps": apps}}}
    (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
    for k, (_size, text) in fixture_blocks(fixture).items():
        (folder / "alone" / f"data_app_{k}.csv").write_text(text, encoding="utf-8")
    return folder


# --- the loader and the metric -------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["a2a_run1_ppn8_apps.csv", "ag_run1_ppn8_apps.csv"])
def test_loader_reads_the_real_october_shape(tmp_path: Path, fixture: str) -> None:
    blocks = fixture_blocks(fixture)
    assert len(blocks) >= 9
    folder = tmp_path / "alone"
    folder.mkdir()
    for k, (size, text) in blocks.items():
        (folder / f"data_app_{k}.csv").write_text(text, encoding="utf-8")
        n, median, p99 = expected_stats(text)
        stat = report_data.read_stats(folder / f"data_app_{k}.csv")
        assert stat == report_data.Stat(n, median, p99), (k, size)
        assert (
            report_data.percentile(report_data.read_samples(folder / f"data_app_{k}.csv"), 99)
            == p99
        )
    # the slowest-rank column is not the median-rank column
    text = blocks[0][1]
    median_column = [float(r.split(",")[5]) for r in text.splitlines()[1:]]
    assert report_data.read_stats(folder / "data_app_0.csv").t_med != statistics.median(
        median_column
    )


def test_loader_on_a_run_folder_in_the_real_shape(tmp_path: Path) -> None:
    folder = october_run(
        tmp_path, "gpu_a2a_comm_same_leaf_2026-10-29_16-04-13-621184", "a2a_run1_ppn8_apps.csv"
    )
    info = report_data.inspect_run(folder, None)
    assert (info.state, info.layout, info.victim) == ("unknown", "?", "a2a")
    data = report_data.load_points(info)
    blocks = fixture_blocks("a2a_run1_ppn8_apps.csv")
    for _k, (size, text) in blocks.items():
        n, median, p99 = expected_stats(text)
        assert data.points[("alone", size)] == report_data.Stat(n, median, p99)
    assert data.points[("alone", 268435456)] is None  # apps 9 and 10 have no csv: FAIL
    assert data.points[("alone", 536870912)] is None


def test_slowdown_math() -> None:
    alone = report_data.summarize([0.001] * 5)
    steady = report_data.summarize([0.0015] * 5)
    assert alone is not None and steady is not None
    assert report_data.slowdown(steady, alone) == 1.5
    ramp = report_data.summarize([i / 1000 for i in range(1, 101)])
    assert ramp is not None
    assert ramp.t_med == 0.0505 and ramp.t_p99 == 0.099 and ramp.n == 100
    assert report_data.slowdown(steady, "-") == "n/a"
    assert report_data.slowdown(steady, "FAIL") == "n/a"
    assert report_data.slowdown("FAIL", alone) == "FAIL"
    assert report_data.slowdown("-", alone) == "-"
    assert report_data.summarize([]) is None


def test_percentile_is_nearest_rank() -> None:
    values = [float(i) for i in range(1, 11)]
    assert report_data.percentile(values, 99) == 10.0
    assert report_data.percentile(values, 50) == 5.0
    assert report_data.percentile([3.0], 99) == 3.0
    hundred = [float(i) for i in range(100, 0, -1)]
    assert report_data.percentile(hundred, 99) == 99.0


def test_names_follow_the_campaign() -> None:
    assert list(report_data.CODES) == [c.name for c in campaign.CONDITIONS]
    assert report_data.VICTIMS == tuple(v.stem for v in campaign.VICTIMS)
    assert report_data.LAYOUTS == tuple(layout.name for layout in campaign.LAYOUTS)
    assert [report_tables.size_label(s) for s in campaign.SIZES] == [
        "16KiB", "64KiB", "256KiB", "1MiB", "4MiB", "16MiB", "64MiB", "256MiB",
    ]  # fmt: skip
    assert report_data.cond_code("steady_a2m") == "s2m"
    assert report_data.cond_code("ppn8_alone_extra") == "ppn8_alo"
    assert report_data.state_abbrev(S1) == "n1a0s1"


def test_newest_attempt_wins_and_is_noted(lab: Lab) -> None:
    old = lab.run_dir("old", {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: [0.009] * 5}})
    new = lab.run_dir("new", {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: [0.0015] * 5}})
    lab.log(
        [
            log_line(1, old, submitted="2026-11-10T09:00:00"),
            log_line(1, new, submitted="2026-11-10T10:00:00", job="2001"),
        ]
    )
    code, lines, _ = lab.call("report")
    assert code == 0
    assert (
        first_page(lines)[0]
        == "VERDICT: OK nplb0_ar0_sr0: 1 points, worst slowdown x1.50 (a2a steady_a128k 16KiB)"
    )
    assert "2 cells had more than one attempt (newest successful used)" in "\n".join(lines)
    # a newer FAILED attempt does not hide an older success
    broken = lab.run_dir("broken", {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: None}})
    lab.log(
        [
            log_line(1, old, submitted="2026-11-10T09:00:00"),
            log_line(1, broken, submitted="2026-11-10T10:00:00", job="2002"),
        ]
    )
    _, lines, _ = lab.call("report")
    assert "x9.00" in "\n".join(lines) and "FAIL" not in "\n".join(table_after(lines, "==")[0])


def test_state_and_layout_do_not_mix(lab: Lab) -> None:
    steady = {"steady_a128k": {16384: [0.0015] * 5}}
    alone = {"alone": {16384: [0.001] * 5}}
    lonely = lab.run_dir("lonely", steady)
    other_state = lab.run_dir("other_state", alone, state=S1)
    other_layout = lab.run_dir("other_layout", alone, layout="shared")
    other_victim = lab.run_dir("other_victim", alone, victim="agtr")
    lab.log(
        [
            log_line(1, lonely),
            log_line(2, other_state, state=S1),
            log_line(3, other_layout, layout="shared"),
            log_line(4, other_victim, victim="agtr"),
        ]
    )
    code, lines, _ = lab.call("report", "--all")
    assert code == 0
    rows = table_after(lines, f"== {S0} | split-i | a2a")
    assert rows[0][:2] == ["size", "alone"]
    assert rows[1][:3] == ["16KiB", "-", "-"] or rows[1][-1] == "n/a"
    assert "n/a" in rows[1] and not any(cell.startswith("x") for cell in rows[1])


def test_slowdown_cells_fail_na_and_not_run(lab: Lab) -> None:
    run1 = lab.run_dir(
        "run1",
        {
            "alone": {16384: [0.001] * 5, 65536: [0.002] * 5},
            "steady_a128k": {16384: [0.0015] * 5, 65536: None},
        },
        n=1,
    )
    run2 = lab.run_dir("run2", {"steady_a2m": {16384: [0.002] * 5}}, n=2)
    lab.log([log_line(1, run1), log_line(2, run2)])
    code, lines, _ = lab.call("report", "--all")
    assert code == 0
    rows = table_after(lines, f"== {S0} | split-i | a2a")
    header = rows[0]
    assert header[:3] == ["size", "alone", "ms"] and header[3] == "Gb/s"
    assert header[4:] == ["s128k", "s2m"]
    first = dict(zip(header[4:], rows[1][3:], strict=True))  # 16 KiB
    second = dict(zip(header[4:], rows[2][3:], strict=True))  # 64 KiB
    assert first == {"s128k": "x1.50", "s2m": "x2.00"}
    assert second == {"s128k": "FAIL", "s2m": "-"}
    assert rows[1][:2] == ["16KiB", "1.00"]
    assert float(rows[1][2]) == float(f"{8 * 16384 * 8 / 0.001 / 1e9:.3g}")  # alone Gb/s


def test_victim_tables(lab: Lab) -> None:
    sizes = [16 * KIB * 4**k for k in range(8)]
    conds = {"alone": {s: [0.01] * 5 for s in sizes}}
    for name in report_data.CODES:
        if name != "alone":
            conds[name] = {s: [0.0152] * 5 for s in sizes}
    a2a = lab.run_dir("a2a_run", conds, n=1)
    agtr = lab.run_dir("agtr_run", conds, n=2, victim="agtr")
    shared = lab.run_dir("shared_run", conds, n=3, layout="shared")
    lab.log(
        [
            log_line(1, a2a),
            log_line(2, agtr, victim="agtr"),
            log_line(3, shared, layout="shared"),
        ]
    )
    code, lines, _ = lab.call("report", "--all")
    assert code == 0
    assert all(len(line) <= 100 for line in lines)
    assert titles(lines)[0].startswith(f"== {S0} | split-i | a2a")
    assert len(titles(lines)) == 3
    rows = table_after(lines, f"== {S0} | split-i | a2a")
    assert rows[0][:4] == ["size", "alone", "ms", "Gb/s"]
    sizes_shown = [r[0] for r in rows if r and r[0][0].isdigit()]
    assert sizes_shown[:8] == [report_tables.size_label(s) for s in sizes]  # ascending
    assert len(sizes_shown) == 16  # two column groups: the 10 conditions do not fit one line
    headers = [r for r in rows if r[0] == "size"]
    assert len(headers) == 2
    shown_codes = [c for h in headers for c in h[4:]]
    assert shown_codes == [report_data.CODES[c] for c in report_data.CODES if c != "alone"]
    assert float(rows[1][1]) == 10.0  # alone ms
    assert float(rows[1][2]) == float(f"{8 * 16 * KIB * 8 / 0.01 / 1e9:.3g}")  # Gb/s
    assert rows[1][3] == "x1.52"
    shared_rows = table_after(lines, f"== {S0} | shared | a2a")
    assert float(shared_rows[1][2]) == float(f"{12 * 16 * KIB * 8 / 0.01 / 1e9:.3g}")
    agtr_rows = table_after(lines, f"== {S0} | split-i | agtr")
    assert agtr_rows[0][:3] == ["size", "alone", "ms"] and "Gb/s" not in agtr_rows[0]
    assert "Gb/s/rank (a2a, remote bytes only)" in "\n".join(lines)


def test_tables_split_by_the_page_width(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    conds = {name: {16384: [0.01] * 5} for name in report_data.CODES}
    folder = lab.run_dir("narrow", conds, n=1, victim="agtr")
    lab.log([log_line(1, folder, victim="agtr")])
    monkeypatch.setenv("PAGE_COLS", "50")
    _, lines, _ = lab.call("report", "--all")
    index = next(i for i, line in enumerate(lines) if line.startswith("== "))
    table = lines[index + 1 : lines.index("", index)] if "" in lines[index:] else lines[index + 1 :]
    table = [line for line in table if line != "\f"]
    assert all(len(line) <= 50 for line in table)
    headers = [line.split() for line in table if line.startswith("size")]
    assert len(headers) >= 3  # 10 codes do not fit 50 columns
    assert [c for h in headers for c in h[3:]] == [
        report_data.CODES[c] for c in report_data.CODES if c != "alone"
    ]
    assert all(h[:3] == ["size", "alone", "ms"] for h in headers)


# --- scopes --------------------------------------------------------------------------------------


def scoped_lab(lab: Lab) -> None:
    ok = {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: [0.0015] * 5}}
    r1 = lab.run_dir("r1", ok, n=1)
    r2 = lab.run_dir("r2", ok, n=2, state=S1)
    r3 = lab.run_dir("r3", ok, n=3, state=S1, layout="shared", victim="agtr")
    lab.log(
        [
            log_line(1, r1),
            log_line(2, r2, state=S1),
            log_line(3, r3, state=S1, layout="shared", victim="agtr"),
            log_line(4, lab.data / "pending", state=S2, status="SUBMITTED", elapsed_s=""),
        ]
    )


def test_scopes(lab: Lab) -> None:
    scoped_lab(lab)
    expected = {
        (): [f"== {S1} | split-i | a2a", f"== {S1} | shared | agtr"],
        ("1",): [f"== {S0} | split-i | a2a"],
        ("2",): [f"== {S1} | split-i | a2a"],
        ("--state", S0): [f"== {S0} | split-i | a2a"],
        ("--state", "n1a0s1"): [f"== {S1} | split-i | a2a", f"== {S1} | shared | agtr"],
        ("--victim", "agtr"): [f"== {S1} | shared | agtr"],
        ("--layout", "shared"): [f"== {S1} | shared | agtr"],
        ("--all",): [
            f"== {S0} | split-i | a2a",
            f"== {S1} | split-i | a2a",
            f"== {S1} | shared | agtr",
        ],
    }
    for args, want in expected.items():
        code, lines, err = lab.call("report", *args)
        assert code == 0, (args, err)
        got = [t.split("  (")[0] for t in titles(lines)]
        assert got == want, args
        assert first_page(lines)[0].startswith("VERDICT: OK "), args
    _, lines, _ = lab.call("report", "--all")
    page = "\n".join(first_page(lines))
    assert S0 in page and S1 in page


def test_unknown_scope_values_exit_2_and_list_the_valid_ones(lab: Lab) -> None:
    scoped_lab(lab)
    for args, valid in [
        (("--state", "nplb9_ar0_sr0"), [S0, S1, "nplb1_ar1_sr1"]),
        (("--victim", "zzz"), ["a2a", "agtr", "ardc"]),
        (("--layout", "zzz"), ["split-i", "split-l", "shared"]),
        (("7",), ["1", "2", "3"]),
        (("x",), ["row number"]),
    ]:
        code, lines, err = lab.call("report", *args)
        assert code == 2, args
        assert lines == [""], args
        assert all(v in err for v in valid), (args, err)
    code, _, err = lab.call("report", "--all", "--state", S0)
    assert code == 2 and "--all" in err


def test_no_finished_runs(lab: Lab) -> None:
    code, lines, _ = lab.call("report")
    assert code == 0 and lines[0] == "VERDICT: WARN no finished runs in the run log"
    lab.log([log_line(1, lab.data / "x", status="SUBMITTED", elapsed_s="")])
    code, lines, _ = lab.call("report")
    assert code == 0 and lines[0] == "VERDICT: WARN no finished runs in the run log"
    code, lines, _ = lab.call("compare")
    assert code == 0 and lines[0].startswith("VERDICT: WARN compare a2a steady_a128k split-i: no")


def test_a_missing_data_dir_is_noted_not_fatal(lab: Lab) -> None:
    good = lab.run_dir(
        "good", {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: [0.002] * 5}}
    )
    lab.log(
        [
            log_line(1, good),
            log_line(2, lab.root / "gone", submitted="2026-11-10T11:00:00"),
            log_line(3, "-", status="SKIPPED", job="-"),
        ]
    )
    code, lines, _ = lab.call("report", "--all")
    assert code == 0
    page = "\n".join(first_page(lines))
    assert first_page(lines)[0].startswith("VERDICT: OK")
    assert "1 run folder missing" in page


def test_relative_data_dirs_use_data_root(lab: Lab) -> None:
    lab.run_dir("rel", {"alone": {16384: [0.001] * 5}, "steady_a128k": {16384: [0.002] * 5}})
    lab.log([log_line(1, "rel")])
    code, lines, _ = lab.call("report")
    assert code == 0 and first_page(lines)[0].startswith("VERDICT: OK")


def test_report_first_page_is_a_summary(lab: Lab) -> None:
    ok = {
        "alone": {16384: [0.001] * 5, 65536: [0.002] * 5},
        "steady_a128k": {16384: [0.0015] * 5, 65536: [0.003] * 5},
        "steady_a2m": {16384: [0.002] * 5, 65536: [0.0045] * 5},
    }
    r1 = lab.run_dir("r1", ok, n=1)
    r2 = lab.run_dir("r2", ok, n=2, state=S1)
    lab.log([log_line(1, r1), log_line(2, r2, state=S1, submitted="2026-11-10T09:00:00")])
    code, lines, _ = lab.call("report")
    assert code == 0
    page = first_page(lines)
    assert len(page) <= 25
    assert page[0] == f"VERDICT: OK {S0}: 4 points, worst slowdown x2.25 (a2a steady_a2m 64KiB)"
    text = "\n".join(page)
    assert re.search(rf"{S0}\s+n0a0s0\s+4 points", text)
    assert f"{S1} (1 run)" in text  # other states of the run log, not in scope
    for code_name in ("s128k", "s2m", "s16m", "b1/1", "b10/10", "b100/100", "b10/30", "b30/10"):
        assert code_name in text
    assert "b10/10@2m" in text and "b10/10@16m" in text
    assert re.search(
        r"a2a\s+x2\.25 s2m split-i 64KiB\s+med 4\.50 ms\s+p99 4\.50 ms\s+alone 2\.00 ms", text
    )
    assert max(len(line) for line in lines) <= 100
    # a FAIL turns the verdict into a WARN and counts it
    bad = lab.run_dir("bad", {"alone": {16384: [0.001] * 5}, "steady_a2m": {16384: None}}, n=3)
    lab.log([log_line(3, bad)])
    _, lines, _ = lab.call("report")
    assert first_page(lines)[0].startswith(f"VERDICT: WARN {S0}: 0 points")
    assert "1 FAIL" in "\n".join(first_page(lines))


def test_alone_only_run_has_a_warn_verdict(lab: Lab) -> None:
    only = lab.run_dir("only", {"alone": {16384: [0.001] * 5}}, n=1)
    lab.log([log_line(1, only)])
    _, lines, _ = lab.call("report")
    assert first_page(lines)[0].startswith(f"VERDICT: WARN {S0}: no slowdown points yet")


def test_run_dir_mode_on_real_shape(lab: Lab) -> None:
    folder = october_run(
        lab.root, "gpu_a2a_comm_same_leaf_2026-10-29_16-04-13-621184", "a2a_run1_ppn8_apps.csv"
    )
    ag = october_run(
        lab.root, "gpu_ag_comm_same_leaf_2026-10-29_16-35-53-182977", "ag_run1_ppn8_apps.csv"
    )
    assert not (folder / "nplb_state.json").exists()
    code, lines, err = lab.call("report", "--run", str(folder), "--run", str(ag))
    assert code == 0, err
    assert first_page(lines)[0] == "VERDICT: WARN unknown: 0 points, 2 FAIL"
    assert titles(lines) == [
        "== unknown | ? | a2a  (0 points)",
        "== unknown | ? | agtr  (0 points)",
    ]
    rows = table_after(lines, "== unknown | ? | a2a")
    blocks = fixture_blocks("a2a_run1_ppn8_apps.csv")
    by_size = {r[0]: r for r in rows[1:]}
    for k in (0, 5, 8):
        size, text = blocks[k]
        median = expected_stats(text)[1] * 1000
        shown = by_size[report_tables.size_label(size)]
        assert float(shown[1]) == float(f"{median:.3g}"), (k, shown)
        assert shown[2] == "-"  # no layout: no Gb/s
    assert by_size["256MiB"][1] == "FAIL" and by_size["512MiB"][1] == "FAIL"
    assert by_size["64MiB"][1] != "FAIL"
    # one bare folder works through the same path as a run of the log
    code, _, _ = lab.call("report", "--run", str(lab.root / "missing"))
    assert code == 2


# --- compare -------------------------------------------------------------------------------------


def compare_lab(lab: Lab) -> None:
    def conds(steady16: float, steady64: float) -> dict:
        return {
            "alone": {16384: [0.001] * 5, 65536: [0.002] * 5},
            "steady_a128k": {16384: [steady16] * 5, 65536: [steady64] * 5},
        }

    r0 = lab.run_dir("r0", conds(0.001, 0.002), n=1, state=S0)
    r1 = lab.run_dir("r1", conds(0.0015, 0.0033), n=2, state=S1)
    r2 = lab.run_dir("r2", conds(0.002, 0.0024), n=3, state=S2)
    lab.log(
        [
            log_line(1, r0, state=S0),
            log_line(2, r1, state=S1),
            log_line(3, r2, state=S2),
        ]
    )


def test_compare_table(lab: Lab) -> None:
    compare_lab(lab)
    code, lines, err = lab.call("compare", "a2a", "steady_a128k", "--layout", "split-i")
    assert code == 0, err
    assert lines[0] == "VERDICT: OK compare a2a steady_a128k split-i: 3 of 8 states have data"
    states = [
        report_data.state_abbrev(report_data.net_state.label(*s))
        for s in report_data.net_state.STATES
    ]
    header = next(line.split() for line in lines if line.startswith("size"))
    assert header == ["size", *states]
    assert states[0] == "n0a0s0" and "n1a0s1" in states
    rows = {
        r[0]: r[1:] for r in (line.split() for line in lines) if r and r[0] in ("16KiB", "64KiB")
    }
    by_state = dict(zip(states, rows["16KiB"], strict=True))
    assert by_state["n0a0s0"] == "x1.00" and by_state["n1a0s1"] == "x1.50"
    assert by_state["n0a1s0"] == "x2.00"
    assert by_state["n1a1s1"] == "-"  # a state without data keeps its column
    assert dict(zip(states, rows["64KiB"], strict=True))["n1a0s1"] == "x1.65"
    assert any("n=NPLB" in line and "a=AR" in line and "s=SR" in line for line in lines)
    assert lines[-2] == f"worst: {S2} at 16KiB x2.00; best: {S0} at 16KiB x1.00"
    assert all(len(line) <= 100 for line in lines)
    # the defaults are a2a, steady_a128k, split-i; either order of the two names
    _, default, _ = lab.call("compare")
    assert default == lines
    _, swapped, _ = lab.call("compare", "steady_a128k", "a2a")
    assert swapped == lines
    _, short, _ = lab.call("compare", "s128k")
    assert short == lines


def test_compare_alone_shows_milliseconds(lab: Lab) -> None:
    compare_lab(lab)
    code, lines, _ = lab.call("compare", "a2a", "alone")
    assert code == 0
    assert lines[0] == "VERDICT: OK compare a2a alone split-i: 3 of 8 states have data"
    rows = {
        r[0]: r[1:] for r in (line.split() for line in lines) if r and r[0] in ("16KiB", "64KiB")
    }
    assert rows["16KiB"][:3] == ["1.00", "-", "1.00"] and rows["64KiB"][0] == "2.00"
    assert not any(line.startswith("worst:") for line in lines)


def test_compare_unknown_names_exit_2(lab: Lab) -> None:
    compare_lab(lab)
    code, _, err = lab.call("compare", "--layout", "zzz")
    assert code == 2 and "split-i" in err and "shared" in err
    for args in (("zzz",), ("a2a", "nope"), ("a2a", "steady_a128k", "extra")):
        code, lines, err = lab.call("compare", *args)
        assert code == 2, args
        assert lines == [""]
        assert "a2a" in err and "steady_a128k" in err
    _, _, err = lab.call("compare", "zzz")
    assert "agtr" in err and "burst_l10ms_p10ms_a2m" in err


# --- pages ---------------------------------------------------------------------------------------


def synthetic_campaign(lab: Lab) -> int:
    sizes = [16 * KIB * 4**k for k in range(8)]
    lines = []
    row = 0
    files = 0
    for state in (report_data.net_state.label(*s) for s in report_data.net_state.STATES):
        for victim in report_data.VICTIMS:
            row += 1
            conds = {}
            for index, cond in enumerate(report_data.CODES):
                conds[cond] = {
                    s: [(0.001 + 0.0001 * i) * (1 + index / 7) for i in range(40)] for s in sizes
                }
                files += len(sizes)
            folder = lab.run_dir(f"b{row}", conds, n=row, state=state, victim=victim)
            lines.append(log_line(row, folder, state=state, victim=victim))
    lab.log(lines)
    return files


def test_everything_fits_pages_for_a_big_campaign(lab: Lab) -> None:
    files = synthetic_campaign(lab)
    assert files == 8 * 3 * 11 * 8
    began = time.monotonic()
    code, lines, err = lab.call("report", "--all")
    spent = time.monotonic() - began
    assert code == 0, err
    assert spent < 10
    raw = "\n".join(lines).encode()
    built = pages.build_pages(raw, pages.Geometry(38, 100))
    assert len(built) > 8
    assert max(len(line) for page in built for line in page) <= 100
    assert not any(line.endswith("~") for page in built for line in page)
    assert max(len(page) for page in built) <= 36
    assert all(page[0].startswith("== ") for page in built[1:])  # no table starts mid-page
    assert len(titles(lines)) == 24
    assert first_page(lines)[0].startswith("VERDICT: OK 8 states: ")
    assert len(first_page(lines)) <= 25
    code, cmp_lines, _ = lab.call("compare", "agtr", "burst_l10ms_p10ms_a16m")
    assert code == 0
    assert cmp_lines[0].endswith("8 of 8 states have data")
    built = pages.build_pages("\n".join(cmp_lines).encode(), pages.Geometry(38, 100))
    assert len(built) == 1 and max(len(line) for line in built[0]) <= 100


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_read_only(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    scoped_lab(lab)

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("report must not start a process or open a socket")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    import socket

    monkeypatch.setattr(socket, "socket", refuse)
    before = tree(lab.root)
    for args in (("report",), ("report", "--all"), ("compare",)):
        code, _, _ = lab.call(*args)
        assert code == 0
    assert tree(lab.root) == before


# --- dispatcher ----------------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


def env_for(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "PAGE_LINES", "PAGE_COLS"):
        env.pop(var, None)
    env.update(
        WORK=str(tmp_path / "work"),
        DATA_ROOT=str(tmp_path / "data"),
        NPLB_REPORT_DIR=str(tmp_path / "reports"),
        NPLB_NOW=NOW,
    )
    return env


def run_nplb(tmp_path: Path, chk: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=env_for(tmp_path),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_dispatcher_report_and_compare(lab: Lab, tmp_path: Path, checkout: Path) -> None:
    compare_lab(lab)
    shown = run_nplb(tmp_path, checkout, "report")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    out = shown.stdout.splitlines()
    assert out[0].startswith("report | page 1/")
    assert out[1].startswith(f"VERDICT: OK {S2}: 2 points")
    assert len(out) <= 38 and max(len(line) for line in out) <= 100
    second = run_nplb(tmp_path, checkout, "page", "2")
    assert second.stdout.splitlines()[0].startswith("report | page 2/")
    assert second.stdout.splitlines()[1].startswith(f"== {S2} | split-i | a2a")
    compared = run_nplb(tmp_path, checkout, "compare", "a2a", "steady_a128k")
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert compared.stdout.splitlines()[0].startswith("compare | page 1/")
    assert "3 of 8 states have data" in compared.stdout.splitlines()[1]
    saved = sorted(
        p.name.split("_", 1)[1] for p in (tmp_path / "reports").iterdir() if p.suffix == ".txt"
    )
    assert saved == ["compare.txt", "report.txt"]
    bad = run_nplb(tmp_path, checkout, "report", "--victim", "zzz")
    assert bad.returncode == 2 and "agtr" in bad.stderr and bad.stdout == ""
    bad = run_nplb(tmp_path, checkout, "compare", "zzz")
    assert bad.returncode == 2 and "steady_a128k" in bad.stderr and bad.stdout == ""
    bad = run_nplb(tmp_path, checkout, "report", "--bogus")
    assert bad.returncode == 2


def test_report_alone_does_not_write_outside_the_pager(
    lab: Lab, tmp_path: Path, checkout: Path
) -> None:
    compare_lab(lab)
    before = {k: v for k, v in tree(tmp_path).items() if not k.startswith("chk/")}
    run_nplb(tmp_path, checkout, "report", "--all")
    after = {k: v for k, v in tree(tmp_path).items() if not k.startswith("chk/")}
    changed = {k for k in after if before.get(k) != after[k]}
    assert changed and all(k.startswith("reports/") for k in changed), changed


def test_help_is_compact_and_lists_every_verb(tmp_path: Path, checkout: Path) -> None:
    helped = run_nplb(tmp_path, checkout, "help")
    assert helped.returncode == 0
    lines = helped.stdout.splitlines()
    assert 0 < len(lines) <= 30
    assert max(len(line) for line in lines) <= 100
    text = helped.stdout
    verbs = (
        "help fix-venv page reports build node topo net campaign next queue follow run cancel skip"
    )
    for verb in [*verbs.split(), "report", "compare"]:
        assert re.search(rf"(?<![\w-]){re.escape(verb)}(?![\w-])", text), verb


# --- python 3.10 ---------------------------------------------------------------------------------


def test_python_310_safe(lab: Lab, tmp_path: Path) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    compare_lab(lab)
    for args in (["report", "--all"], ["compare", "a2a", "steady_a128k"]):
        result = subprocess.run(
            [python310, str(REPORT_PY), *args],
            env=env_for(tmp_path),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[0].startswith("VERDICT: OK")
