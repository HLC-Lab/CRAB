#!/usr/bin/env python3
"""Summary tables of the NPLB campaign from CRAB data directories (needs pandas).

Reads the data dirs of runs made with the configs of make_configs.py and writes, into `--out`,
`summary.csv` (one row per data dir, experiment, collected app and message size) and `summary.md`
(readable tables, and C0 against C2 side by side).

    python analyze.py DATA_DIR... --out DIR

Per-rank bandwidth is `msg_size * grty * 8 / t_med`: Blink times one window of `-grty` operations
(default 1) per sample and records the whole window, except ping-pong, which records the window's
round trips divided by 2 (one-way). Spread is max / min over ranks within one iteration.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import experiment_name, parse_experiment_name  # noqa: E402

COLUMNS = [
    "data_dir",
    "file",
    "set",
    "smoke",
    "experiment",
    "phase",
    "test",
    "victim",
    "aggressor",
    "net",
    "paths",
    "app",
    "program",
    "grty",
    "msg_size",
    "n_runs",
    "n_iters",
    "t_med",
    "t_p99",
    "t_max_p99",
    "bw_gbps",
    "spread_med",
    "spread_p99",
    "spread_skipped",
    "run_cv",
    "slowdown_med",
    "slowdown_p99",
]
NAN = float("nan")
DASH = "–"
_FILE_NAME = re.compile(r"^(?P<smoke>smoke_)?(?P<set>md|col)_(?P<rest>.+)$")
_GRTY = re.compile(r"(?:^|\s)-grty\s+(\d+)")
BW = ".3g"  # bandwidth cells: three significant digits (small sizes are tiny)
SIZE_UNITS = (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10))


@dataclass
class Report:
    """What the run read and what it left out."""

    dirs: int = 0
    experiments: set[tuple[str, str]] = field(default_factory=set)
    csvs: int = 0
    skipped: list[str] = field(default_factory=list)
    replaced: int = 0


def human_size(size: int) -> str:
    """8 -> "8 B", 32768 -> "32 KiB", 2097152 -> "2 MiB"; a size that is no whole unit stays bytes."""
    for unit, factor in SIZE_UNITS:
        if size >= factor and size % factor == 0:
            return f"{size // factor} {unit}"
    return f"{size} B"


def app_grty(args: str) -> int:
    """The `-grty` of an app's arguments (operations per timed sample); 1 when absent."""
    found = _GRTY.search(args)
    return max(1, int(found[1])) if found else 1


def file_fields(file_name: str) -> tuple[str, bool, str | None]:
    """(set, smoke, key) of a generated file stem: key is the colleague test (`a2a`) or None."""
    found = _FILE_NAME.match(file_name)
    if found is None:
        return "?", False, None
    key = found["rest"].split("_")[0] if found["set"] == "col" else None
    return found["set"], found["smoke"] is not None, key


def size_metrics(group: pd.DataFrame, app: int, msg_size: int, grty: int) -> dict[str, Any]:
    """The metrics of one app's rows for one message size (pure: no IO)."""
    med = group[f"{app}_Median-Duration_s"]
    low, high = group[f"{app}_Min-Duration_s"], group[f"{app}_Max-Duration_s"]
    t_med = float(med.median())
    valid = low > 0
    spread = (high[valid] / low[valid]).astype(float)
    run_medians = med.groupby(group["run_id"]).median()
    mean = float(run_medians.mean())
    cv = float(run_medians.std(ddof=1)) / mean if len(run_medians) >= 2 and mean > 0 else NAN
    return {
        "n_runs": int(group["run_id"].nunique()),
        "n_iters": len(group),
        "t_med": t_med,
        "t_p99": float(med.quantile(0.99)),
        "t_max_p99": float(high.quantile(0.99)),
        "bw_gbps": msg_size * grty * 8 / t_med / 1e9 if t_med > 0 else NAN,
        "spread_med": float(spread.median()) if len(spread) else NAN,
        "spread_p99": float(spread.quantile(0.99)) if len(spread) else NAN,
        "spread_skipped": int((~valid).sum()),
        "run_cv": cv,
    }


def read_app_csv(path: Path, app: int) -> pd.DataFrame | str:
    """The CSV of one app, or the reason it cannot be used."""
    if not path.is_file():
        return f"missing CSV {path.name}"
    try:
        df = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
        return f"unreadable CSV {path.name}"
    if df.empty:
        return f"empty CSV {path.name}"
    needed = {"run_id", "msg_size"} | {f"{app}_{k}-Duration_s" for k in ("Min", "Max", "Median")}
    if not needed <= set(df.columns):
        return f"CSV {path.name} lacks columns: {', '.join(sorted(needed - set(df.columns)))}"
    return df


def read_dir(path: Path, report: Report) -> list[dict[str, Any]]:
    """The summary rows of one data dir; what is left out goes into the report."""
    try:
        config = json.loads((path / "config.json").read_text())
        file_name = str(config.get("global_options", {}).get("name") or path.name)
        experiments = config["experiments"]
    except (OSError, ValueError, KeyError, AttributeError):
        report.skipped.append(f"{path.name}: no readable config.json with experiments")
        return []
    report.dirs += 1
    set_name, smoke, key = file_fields(file_name)
    rows: list[dict[str, Any]] = []
    for exp, body in experiments.items():
        apps = {k: a for k, a in body.get("apps", {}).items() if a.get("collect")}
        if not apps:
            continue
        parsed = parse_experiment_name(exp)
        if parsed is None:
            report.skipped.append(f"{path.name}: {exp}: unknown experiment name")
            continue
        for app_id, app in apps.items():
            df = read_app_csv(path / exp / f"data_app_{app_id}.csv", int(app_id))
            if isinstance(df, str):
                report.skipped.append(f"{path.name}: {exp}: {df}")
                continue
            report.experiments.add((path.name, exp))
            report.csvs += 1
            grty = app_grty(str(app.get("args", "")))
            for msg_size, group in df.groupby("msg_size"):
                ident = {
                    "data_dir": path.name,
                    "file": file_name,
                    "set": set_name,
                    "smoke": smoke,
                    "experiment": exp,
                    **parsed,
                    "app": int(app_id),
                    "program": app.get("path", ""),
                    "grty": grty,
                    "msg_size": int(msg_size),
                }
                ident["victim"] = parsed["victim"] or key
                rows.append({**ident, **size_metrics(group, int(app_id), int(msg_size), grty)})
    return rows


def add_slowdown(rows: list[dict[str, Any]]) -> None:
    """Fill slowdown_med and slowdown_p99 from the `*_alone` row of the same file and size."""
    alone = {
        (r["data_dir"], r["file"], r["experiment"], r["msg_size"]): r
        for r in rows
        if r["phase"] == "p2" and r["aggressor"] is None
    }
    for r in rows:
        r["slowdown_med"] = r["slowdown_p99"] = NAN
        if r["phase"] != "p2":
            continue
        name = experiment_name("p2", f"{r['victim']}_alone", r["net"], r["paths"])
        base = alone.get((r["data_dir"], r["file"], name, r["msg_size"]))
        if base is not None and base["t_med"] > 0 and base["t_p99"] > 0:
            r["slowdown_med"] = r["t_med"] / base["t_med"]
            r["slowdown_p99"] = r["t_p99"] / base["t_p99"]


def build_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """All rows as a table in the stable column order, sorted by file, experiment, size."""
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.sort_values(["file", "experiment", "msg_size", "data_dir"]).reset_index(drop=True)


def fmt(value: Any, spec: str = ".2f") -> str:
    """A number for a table cell; a missing one is a dash."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return DASH
    return format(value, spec)


def md_table(header: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cells) + " |" for cells in body]
    return "\n".join(lines)


def aggressor_label(r: dict[str, Any]) -> str:
    if isinstance(r["aggressor"], str):
        return r["aggressor"]
    return "alone" if r["phase"] == "p2" else DASH


def main_tables(df: pd.DataFrame) -> list[str]:
    """One table per (set, phase, victim): a row per experiment and message size."""
    out: list[str] = []
    header = ["dir", "net", "P", "aggressor", "size", "bw_gbps", "spread_med", "spread_p99"]
    header += ["run_cv", "slowdown_med", "slowdown_p99"]
    for (smoke, set_name, phase, victim), group in df.groupby(
        ["smoke", "set", "phase", "victim"], dropna=False
    ):
        records = group.to_dict("records")
        records.sort(
            key=lambda r: (
                r["data_dir"],
                r["net"],
                r["paths"],
                aggressor_label(r) != "alone",
                aggressor_label(r),
                r["msg_size"],
            )
        )
        body = [
            [
                r["data_dir"],
                r["net"],
                str(r["paths"]),
                aggressor_label(r),
                human_size(r["msg_size"]),
                fmt(r["bw_gbps"], BW),
                fmt(r["spread_med"]),
                fmt(r["spread_p99"]),
                fmt(r["run_cv"], ".3f"),
                fmt(r["slowdown_med"]),
                fmt(r["slowdown_p99"]),
            ]
            for r in records
        ]
        title = f"## {set_name}{' (smoke)' if smoke else ''} {phase} {victim}"
        out.append(f"{title}\n\n{md_table(header, body)}")
        if set_name == "col":
            out[-1] += f"\n\nNote: no alone baseline in this set, so slowdown is {DASH}."
    return out


def cell(by_net: dict[str, pd.DataFrame], net: str, size: int, column: str) -> Any:
    """One value of the rows of a network config (indexed by size), NaN when it has no such size."""
    frame = by_net[net]
    return frame.at[size, column] if size in frame.index else NAN


def c0_c2_tables(df: pd.DataFrame, report: Report) -> list[str]:
    """One table per (set, phase, victim, test, paths) that ran on both C0 and C2."""
    out: list[str] = []
    keys = ["smoke", "set", "phase", "victim", "test", "paths"]
    both = df[df["net"].isin(["C0", "C2"])]
    for (smoke, set_name, phase, victim, test, paths), group in both.groupby(keys, dropna=False):
        if set(group["net"]) != {"C0", "C2"}:
            continue
        kept = group.sort_values("data_dir").drop_duplicates(["net", "msg_size"], keep="last")
        report.replaced += len(group) - len(kept)
        by_net = {net: g.set_index("msg_size") for net, g in kept.groupby("net")}

        body = []
        for size in sorted(set(kept["msg_size"])):
            c0, c2 = (cell(by_net, n, size, "bw_gbps") for n in ("C0", "C2"))
            ratio = c2 / c0 if c0 > 0 and not math.isnan(c2) else NAN
            body.append(
                [
                    human_size(size),
                    fmt(c0, BW),
                    fmt(c2, BW),
                    fmt(ratio),
                    *(fmt(cell(by_net, n, size, "spread_med")) for n in ("C0", "C2")),
                    *(fmt(cell(by_net, n, size, "run_cv"), ".3f") for n in ("C0", "C2")),
                    *(fmt(cell(by_net, n, size, "slowdown_med")) for n in ("C0", "C2")),
                ]
            )
        header = ["size", "bw C0", "bw C2", "bw C2/C0", "spread C0", "spread C2"]
        header += ["cv C0", "cv C2", "slowdown C0", "slowdown C2"]
        label = f"{set_name}{' (smoke)' if smoke else ''} {phase} {victim} {test}, P{paths}"
        out.append(f"### C0 vs C2: {label}\n\n{md_table(header, body)}")
    return out


def markdown(df: pd.DataFrame, report: Report) -> str:
    """The text of summary.md."""
    intro = (
        "# NPLB campaign summary\n\n"
        "- bw_gbps: per-rank bandwidth, msg_size x grty x 8 / median time; for ping-pong, one-way.\n"
        "- spread: max / min over ranks within one iteration (median and p99 over iterations).\n"
        "- run_cv: std / mean over the CRAB runs of each run's median time.\n"
        "- slowdown: co-run time / `*_alone` time of the same file and size (median and p99).\n"
        f"- {DASH}: not available (no alone baseline, one run, or no such size on that network).\n"
    )
    if df.empty:
        return intro + "\nNo data: no experiment of the given directories could be read.\n"
    comparison = c0_c2_tables(df, report)
    sections = main_tables(df)
    if comparison:
        sections += ["# C0 vs C2\n\nbw C2/C0 above 1 means C2 is faster.", *comparison]
    return intro + "\n" + "\n\n".join(sections) + "\n"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def print_report(report: Report, rows: int, out: Path) -> None:
    print(
        f"Read {plural(report.dirs, 'data dir')}, parsed {plural(len(report.experiments), 'experiment')} "
        f"({plural(report.csvs, 'CSV file')}), {plural(rows, 'summary row')}."
    )
    if report.replaced:
        print(f"C0 vs C2: {plural(report.replaced, 'older row')} replaced by a later data dir.")
    if report.skipped:
        print(f"Skipped {len(report.skipped)}:")
        for line in report.skipped:
            print(f"  {line}")
    print(f"Wrote {out / 'summary.csv'} and {out / 'summary.md'}.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Summary tables of the NPLB campaign.")
    ap.add_argument("data_dirs", nargs="+", type=Path, metavar="DATA_DIR", help="CRAB data dirs")
    ap.add_argument("--out", type=Path, required=True, help="directory for summary.csv and .md")
    args = ap.parse_args(argv)
    report = Report()
    rows: list[dict[str, Any]] = []
    for path in args.data_dirs:
        rows += read_dir(path, report)
    add_slowdown(rows)
    df = build_frame(rows)
    text = markdown(df, report)
    args.out.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out / "summary.csv", index=False)
    (args.out / "summary.md").write_text(text)
    print_report(report, len(df), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
