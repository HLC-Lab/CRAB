#!/usr/bin/env python3
"""PNG charts of the NPLB campaign from the `summary.csv` of analyze.py (needs pandas, matplotlib).

    python plot.py SUMMARY_CSV --out DIR [--only bw|spread|slowdown ...]

Writes into `--out`, one chart per group, comparing the network configs (C0 = ECMP, C2 = NPLB,
C3 = NPLB without selective repeat):

* `bw_<set>_<phase>_<victim>_<aggressor or alone>_P<paths>.png`: bandwidth per rank vs message size
* `spread_<same>.png`: max / min time across ranks (median solid, p99 dashed)
* `slowdown_<set>_p2_<victim>_P<paths>.png`: slowdown vs alone, one panel per aggressor

Smoke data gets a `smoke_` file name prefix. matplotlib is not a CRAB dependency: it is imported
when the script starts and, if missing, the script exits 2 with an install hint. Data preparation
(`build_charts`) is pure and never touches matplotlib; `draw` does the drawing.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import COLUMNS, human_size  # noqa: E402

HINT = "plot.py needs matplotlib: pip install matplotlib (into the Python you run this with)"
KINDS = ("bw", "spread", "slowdown")

# Fixed per network, never cycled; a marker too, because aqua is below 3:1 contrast on white.
NET_STYLE = {"C0": ("#2a78d6", "o"), "C2": ("#eb6834", "s"), "C3": ("#1baf7a", "^")}
UNKNOWN_STYLE = ("#6f6e69", "x")
NET_LABEL = {"C0": "C0 (ECMP)", "C2": "C2 (NPLB)", "C3": "C3 (NPLB, no selective repeat)"}
VICTIM_LABEL = {
    "perm": "Pairwise rotation",
    "a2a": "All-to-all",
    "ag": "All-gather",
    "agtr": "All-gather",
    "ardc": "All-reduce",
    "cross": "Ping-pong across spines",
    "local": "Ping-pong within a leaf",
}
# Tick candidates on the message-size axis (the sizes of the campaign); only those present are shown.
TICK_SIZES = [1, 8, 64, 512, 1 << 12, 1 << 15, 1 << 18, 1 << 21, 1 << 24, 1 << 27, 1 << 29]
GROUP_COLUMNS = ["smoke", "set", "phase", "victim", "agg", "net", "paths", "msg_size"]
# kind, (value column, p99 column), what the title says, y label, y scale, reference line
LEVELS: list[tuple[str, tuple[str, str | None], str, str, str, float | None]] = [
    ("bw", ("bw_gbps", None), "bandwidth per rank", "bandwidth per rank (Gb/s)", "log", None),
    (
        "spread",
        ("spread_med", "spread_p99"),
        "spread across ranks",
        "max/min time across ranks",
        "linear",
        1.0,
    ),
]
_BURST = re.compile(r"^burst-([0-9.]+)-([0-9.]+)$")

INK, MUTED, GRID = "#1f1f1e", "#5f5e5a", "#e4e3dd"
STYLE = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "text.color": INK,
    "axes.titlecolor": INK,
    "axes.labelcolor": MUTED,
    "axes.edgecolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "lines.linewidth": 2,
    "lines.markersize": 6,
    "legend.frameon": False,
    "font.size": 10,
}


@dataclass
class Series:
    """One network's line: sizes in x, values in y (NaN leaves a gap), p99 as a dashed twin."""

    net: str
    label: str
    color: str
    marker: str
    x: list[int]
    y: list[float]
    y_p99: list[float] | None = None


@dataclass
class Panel:
    title: str  # empty for a single-panel chart
    series: list[Series]


@dataclass
class Chart:
    """Everything `draw` needs for one PNG."""

    kind: str
    file: str
    title: str
    ylabel: str
    yscale: str  # "log" or "linear"
    reference: float | None  # a thin horizontal line, e.g. 1.0 for a ratio
    ticks: list[tuple[int, str]]
    panels: list[Panel] = field(default_factory=list)


def size_ticks(sizes: list[int]) -> list[tuple[int, str]]:
    """(position, label) of the x ticks: the campaign's sizes present in the data, else all."""
    present = sorted(set(sizes))
    chosen = [s for s in TICK_SIZES if s in present] or present
    return [(s, human_size(s)) for s in chosen]


def pretty_aggressor(name: str) -> str:
    """`burst-0.01-0.0001` -> "burst 0.01 s on, 0.0001 s off"; other names stay as they are."""
    found = _BURST.match(name)
    return f"burst {found[1]} s on, {found[2]} s off" if found else name


def path_count(paths: int) -> str:
    return f"{paths} path" if paths == 1 else f"{paths} paths"


def safe(text: str) -> str:
    """A string for a file name: lowercase, letters, digits, `.` and `-` only."""
    return re.sub(r"[^a-z0-9.-]+", "-", text.lower())


def finite(value: float) -> bool:
    return not math.isnan(value) and not math.isinf(value)


def net_order(net: str) -> tuple[int, str]:
    known = list(NET_STYLE)
    return (known.index(net), net) if net in known else (len(known), net)


def make_series(frame: pd.DataFrame, column: str, p99_column: str | None) -> list[Series]:
    """One series per net in the frame (known nets first); a net with nothing to draw is left out."""
    out: list[Series] = []
    for net in sorted(set(frame["net"]), key=net_order):
        rows = frame[(frame["net"] == net) & (frame["msg_size"] > 0)].sort_values("msg_size")
        y = [float(v) for v in rows[column]]
        y_p99 = [float(v) for v in rows[p99_column]] if p99_column else None
        if not any(finite(v) for v in y + (y_p99 or [])):
            continue
        color, marker = NET_STYLE.get(net, UNKNOWN_STYLE)
        x = [int(v) for v in rows["msg_size"]]
        out.append(Series(net, NET_LABEL.get(net, net), color, marker, x, y, y_p99))
    return out


def prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """The table with the columns the charts group by, and one row per (group, net, size).

    Returns the table and the number of older duplicate rows dropped (the last data dir wins).
    """
    out = df.copy()
    out["agg"] = [a if isinstance(a, str) else "alone" for a in out["aggressor"]]
    out["victim"] = [v if isinstance(v, str) else "?" for v in out["victim"]]
    out["smoke"] = out["smoke"].astype(bool)
    kept = out.sort_values("data_dir", kind="stable").drop_duplicates(GROUP_COLUMNS, keep="last")
    return kept, len(out) - len(kept)


def level_charts(df: pd.DataFrame, kinds: set[str], notes: list[str]) -> list[Chart]:
    """Bandwidth and spread charts: one per group without an aggressor (and per colleague burst)."""
    base = df[df["aggressor"].isna() | (df["set"] == "col")]
    charts: list[Chart] = []
    keys = ["smoke", "set", "phase", "victim", "agg", "paths"]
    for (smoke, set_name, phase, victim, agg, paths), group in base.groupby(keys, sort=True):
        what = "alone" if agg == "alone" else f"against {pretty_aggressor(agg)}"
        name = f"{set_name}_{phase}_{safe(victim)}_{safe(agg)}_P{paths}.png"
        title = f"{VICTIM_LABEL.get(victim, victim)}, {what}, {path_count(paths)}"
        title += " (smoke)" if smoke else ""
        ticks = size_ticks(list(group["msg_size"]))
        for kind, columns, what_it_is, ylabel, yscale, reference in LEVELS:
            if kind not in kinds:
                continue
            file = f"{'smoke_' if smoke else ''}{kind}_{name}"
            series = make_series(group, *columns)
            if not series:
                notes.append(f"skipped {file}: no values")
                continue
            title_kind = f"{title}: {what_it_is}"
            panel = [Panel("", series)]
            charts.append(Chart(kind, file, title_kind, ylabel, yscale, reference, ticks, panel))
    return charts


def slowdown_charts(df: pd.DataFrame, notes: list[str]) -> list[Chart]:
    """Slowdown charts: one per (set, victim, paths) of phase 2, one panel per aggressor."""
    base = df[(df["phase"] == "p2") & df["aggressor"].notna()]
    charts: list[Chart] = []
    for (smoke, set_name, victim, paths), group in base.groupby(
        ["smoke", "set", "victim", "paths"], sort=True
    ):
        file = f"{'smoke_' if smoke else ''}slowdown_{set_name}_p2_{safe(victim)}_P{paths}.png"
        aggressors = sorted(set(group["agg"]), key=lambda a: (a.startswith("burst"), a))
        panels = []
        for agg in aggressors:
            series = make_series(group[group["agg"] == agg], "slowdown_med", "slowdown_p99")
            if series:
                panels.append(Panel(pretty_aggressor(agg), series))
        if not panels:
            notes.append(f"skipped {file}: no values")
            continue
        title = f"{VICTIM_LABEL.get(victim, victim)} against each aggressor, {path_count(paths)}"
        title += " (smoke)" if smoke else ""
        charts.append(
            Chart(
                "slowdown",
                file,
                f"{title}: slowdown vs alone",
                "slowdown vs alone",
                "linear",
                1.0,
                size_ticks(list(group["msg_size"])),
                panels,
            )
        )
    return charts


def build_charts(df: pd.DataFrame, kinds: set[str]) -> tuple[list[Chart], list[str]]:
    """The chart specs of the chosen kinds from a `prepare`d table, and notes on skipped groups."""
    notes: list[str] = []
    charts = level_charts(df, kinds, notes)
    if "slowdown" in kinds:
        charts += slowdown_charts(df, notes)
    return charts, notes


def read_summary(path: Path) -> pd.DataFrame | str:
    """The summary table, or the reason it cannot be used."""
    try:
        df = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
        return f"cannot read {path}"
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        return f"{path} lacks columns: {', '.join(missing)}"
    return df


def load_pyplot() -> Any:
    """matplotlib.pyplot with the file-only backend, or None when matplotlib is not installed."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    return plt


def draw(plt: Any, chart: Chart, path: Path) -> None:
    """Render one chart to a PNG (150 dpi)."""
    count = len(chart.panels)
    cols = min(3, count)
    rows = math.ceil(count / cols)
    size = (8, 5) if count == 1 else (4.4 * cols + 0.8, 3.4 * rows + 1.4)
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(
            rows,
            cols,
            figsize=size,
            dpi=150,
            squeeze=False,
            sharex=True,
            sharey=True,
            layout="constrained",
        )
        flat = list(axes.flat)
        for ax, panel in zip(flat, chart.panels, strict=False):
            ax.set_xscale("log", base=2)
            ax.set_yscale(chart.yscale)
            if chart.reference is not None:
                ax.axhline(chart.reference, color=MUTED, linewidth=0.8, zorder=1)
            for s in panel.series:
                ax.plot(s.x, s.y, color=s.color, marker=s.marker, clip_on=False, zorder=3)
                if s.y_p99 is not None:
                    dashed = {"linestyle": "--", "markerfacecolor": "white"}
                    ax.plot(
                        s.x,
                        s.y_p99,
                        color=s.color,
                        marker=s.marker,
                        clip_on=False,
                        zorder=3,
                        **dashed,
                    )
            ax.set_xticks([p for p, _ in chart.ticks], [t for _, t in chart.ticks])
            ax.minorticks_off()
            ax.tick_params(axis="x", labelrotation=45, labelsize=8, labelbottom=True)
            if panel.title:
                ax.set_title(panel.title, fontsize=10)
        for ax in flat[count:]:
            ax.set_visible(False)
        limits_for(chart, flat[0])
        for ax in axes[:, 0]:
            ax.set_ylabel(chart.ylabel)
        for col in range(cols):
            bottom = max(i for i in range(rows) if i * cols + col < count)
            axes[bottom, col].set_xlabel("message size")
        fig.suptitle(chart.title, fontsize=12)
        add_legend(plt, fig, chart)
        fig.savefig(path, dpi=150)
        plt.close(fig)


def limits_for(chart: Chart, ax: Any) -> None:
    """Spread starts at 1; a slowdown axis always contains the 1.0 reference line."""
    low, high = ax.get_ylim()
    if chart.kind == "spread":
        ax.set_ylim(1.0, max(high, 1.1))
    elif chart.kind == "slowdown":
        ax.set_ylim(min(low, 0.95), max(high, 1.05))


def add_legend(plt: Any, fig: Any, chart: Chart) -> None:
    """One legend for the whole figure: the nets, and what solid and dashed mean when both show."""
    nets: dict[str, Series] = {}
    for panel in chart.panels:
        for s in panel.series:
            nets.setdefault(s.net, s)
    handles = [
        plt.Line2D([], [], color=s.color, marker=s.marker, label=s.label)
        for s in sorted(nets.values(), key=lambda s: net_order(s.net))
    ]
    if any(s.y_p99 is not None for s in nets.values()):
        handles += [
            plt.Line2D([], [], color=MUTED, label="median"),
            plt.Line2D([], [], color=MUTED, linestyle="--", label="p99"),
        ]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PNG charts of the NPLB campaign.")
    ap.add_argument("summary", type=Path, metavar="SUMMARY_CSV", help="summary.csv of analyze.py")
    ap.add_argument("--out", type=Path, required=True, help="directory for the PNG files")
    ap.add_argument("--only", nargs="+", choices=KINDS, help="chart kinds to write (default: all)")
    args = ap.parse_args(argv)
    plt = load_pyplot()
    if plt is None:
        print(HINT, file=sys.stderr)
        return 2
    df = read_summary(args.summary)
    if isinstance(df, str):
        print(df, file=sys.stderr)
        return 2
    df, dropped = prepare(df)
    if dropped:
        print(f"Note: {dropped} older duplicate row(s) dropped (the last data dir wins).")
    charts, notes = build_charts(df, set(args.only or KINDS))
    args.out.mkdir(parents=True, exist_ok=True)
    for chart in charts:
        draw(plt, chart, args.out / chart.file)
        print(f"wrote {args.out / chart.file}")
    for note in notes:
        print(f"note: {note}")
    print(f"Wrote {len(charts)} chart(s), skipped {len(notes)} group(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
