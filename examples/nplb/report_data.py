"""The data side of `nplb report` and `nplb compare`: run folders in, slowdown numbers out (stdlib only).

A run folder is what `nplb run` leaves: `config.json` (the experiments; in each, the apps that carry
`-msgsize S` and are collected are the victim's size sweep), one folder per experiment holding
`data_app_<k>.csv` for app k (a header such as `run_id,msg_size,<k>_Avg-Duration_s,<k>_Min-Duration_s,
<k>_Max-Duration_s,<k>_Median-Duration_s,<k>_MainRank-Duration_s`, one row per measured sample), and
`nplb_state.json` / `schedule_row.json` when `nplb run` made it. A failed app has no csv.

Metrics, all from the `Max-Duration_s` column (the slowest rank: a collective ends when its last rank
does): `t_med` is the median of the samples, `t_p99` their 99th percentile by nearest rank (the value
at sorted position ceil(0.99 n)); `slowdown` = `t_med(condition) / t_med(alone)` of the same state,
layout, victim and size. A cell is `FAIL` when its csv is missing in a run that finished, `n/a` when
there is no usable `alone` to divide by, `-` when no run measured it. Several attempts of one cell:
the newest successful one (by `submitted`) wins. For a2a, `a2a_gbps` is the alone per-rank remote
bandwidth, remote_ranks x size x 8 / t_med / 1e9 (the victim ranks on the other nodes).

Pure functions (`summarize`, `percentile`, `slowdown`, `build_cells`, `groups`, `analyse`) are kept
apart from the IO (`read_samples`, `inspect_run`, `load_points`). Importable by report.py and
report_tables.py. Python 3.10.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402

# the campaign's conditions in its order (campaign.CONDITIONS; a test keeps them equal) and their codes
CODES = {
    "alone": "alone",
    "steady_a128k": "s128k",
    "steady_a2m": "s2m",
    "steady_a16m": "s16m",
    "burst_l1ms_p1ms_a128k": "b1/1",
    "burst_l10ms_p10ms_a128k": "b10/10",
    "burst_l100ms_p100ms_a128k": "b100/100",
    "burst_l10ms_p30ms_a128k": "b10/30",
    "burst_l30ms_p10ms_a128k": "b30/10",
    "burst_l10ms_p10ms_a2m": "b10/10@2m",
    "burst_l10ms_p10ms_a16m": "b10/10@16m",
}
VICTIMS = ("a2a", "agtr", "ardc")
LAYOUTS = ("split-i", "split-l", "shared")
VICTIM_ALIASES = {"a2a": "a2a", "ag": "agtr", "agtr": "agtr", "ardc": "ardc"}
# victim ranks on the other nodes of the victim: 16 ranks on 2 nodes, or 16 ranks on 4 nodes
REMOTE_RANKS = {"split-i": 8, "split-l": 8, "shared": 12}
STATE_LABELS = [net_state.label(*bits) for bits in net_state.STATES]
UNKNOWN_STATE = "unknown"
FAILED = "FAIL"
NO_BASE = "n/a"
NOT_RUN = "-"
SIZE_RE = re.compile(r"(?:^|\s)-msgsize\s+(\d+)")
STATE_RE = re.compile(r"^nplb(\d)_ar(\d)_sr(\d)$")
ABBREV_RE = re.compile(r"^n(\d)a(\d)s(\d)$")

Key = tuple[str, str, str, str, int]  # state, layout, victim, condition, size


class RunError(Exception):
    """A run folder that cannot be read; the message says why (the report notes it and goes on)."""


@dataclass(frozen=True)
class Stat:
    n: int  # samples
    t_med: float  # seconds
    t_p99: float


@dataclass(frozen=True)
class Meta:
    """What the run log says about one attempt (a bare folder: n None, state unknown)."""

    n: int | None
    state: str
    layout: str
    victim: str
    submitted: str
    status: str
    order: int  # position in the log, breaks ties between equal `submitted`


@dataclass(frozen=True)
class Experiment:
    name: str
    apps: tuple[tuple[str, int], ...]  # (app key, victim message size) of the collected apps


@dataclass(frozen=True)
class RunInfo:
    """A run folder after the cheap reads (json only): where it belongs and what it should hold."""

    path: Path
    meta: Meta
    state: str
    layout: str
    victim: str
    experiments: tuple[Experiment, ...]


@dataclass(frozen=True)
class RunData:
    info: RunInfo
    points: dict[tuple[str, int], Stat | None]  # (condition, size) -> stats, None: csv missing


@dataclass(frozen=True)
class Cells:
    values: dict[Key, Stat | None]
    attempts: dict[Key, int]

    @property
    def repeated(self) -> int:
        """How many cells were measured by more than one attempt."""
        return sum(1 for count in self.attempts.values() if count > 1)


@dataclass(frozen=True)
class Group:
    """One table: a state, layout and victim, with the sizes and conditions that were measured."""

    state: str
    layout: str
    victim: str
    sizes: tuple[int, ...]
    conditions: tuple[str, ...]  # without alone


@dataclass(frozen=True)
class Point:
    """One cell with a number: its condition's stats, the alone stats and the slowdown."""

    key: Key
    value: float
    cond: Stat
    alone: Stat


@dataclass(frozen=True)
class Analysis:
    points: list[Point]
    fails_by_state: dict[str, int]  # cells (alone included) whose csv is missing in a finished run
    no_base: int  # condition cells with data but no alone to divide by

    @property
    def fails(self) -> int:
        return sum(self.fails_by_state.values())


# --- names ---------------------------------------------------------------------------------------


def cond_code(name: str) -> str:
    """The column code of a condition; one that is not in the campaign list: its first 8 characters."""
    return CODES.get(name, name[:8])


def state_abbrev(label: str) -> str:
    """nplb1_ar0_sr1 -> n1a0s1; another label: its first 6 characters."""
    match = STATE_RE.match(label)
    return f"n{match.group(1)}a{match.group(2)}s{match.group(3)}" if match else label[:6]


def state_from_text(text: str) -> str:
    """A label as typed: the full label or its abbreviation (n1a0s1), else the text itself."""
    match = ABBREV_RE.match(text)
    if match:
        return f"nplb{match.group(1)}_ar{match.group(2)}_sr{match.group(3)}"
    return text


def order_of(name: str, known: tuple[str, ...] | list[str]) -> tuple[int, str]:
    """Sort key: names of the campaign first, in its order, then the others by name."""
    return (known.index(name), "") if name in known else (len(known), name)


# --- samples -------------------------------------------------------------------------------------


def percentile(values: list[float], pct: int) -> float:
    """Nearest rank: the value at sorted position ceil(pct n / 100). `values` must not be empty."""
    ordered = sorted(values)
    rank = -(-pct * len(ordered) // 100)
    return ordered[max(rank, 1) - 1]


def median(ordered: list[float]) -> float:
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def summarize(values: list[float]) -> Stat | None:
    if not values:
        return None
    ordered = sorted(values)
    return Stat(len(ordered), median(ordered), percentile(ordered, 99))


def read_samples(path: Path) -> list[float]:
    """The `Max-Duration_s` column of one data_app csv, in seconds; [] when unreadable or absent."""
    values: list[float] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            column = next((i for i, name in enumerate(header) if name.endswith("Max-Duration_s")), -1)
            if column < 0:
                return []
            for row in reader:
                try:
                    value = float(row[column])
                except (IndexError, ValueError):
                    continue
                if math.isfinite(value):
                    values.append(value)
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    return values


def read_stats(path: Path) -> Stat | None:
    return summarize(read_samples(path))


def slowdown(cond: Stat | str, alone: Stat | str) -> float | str:
    """cond / alone medians, or FAIL / `-` of the cell, or `n/a` without a usable alone.

    A cell is a Stat, or FAIL (its csv is missing) or `-` (not run); see `lookup`.
    """
    if isinstance(cond, str):
        return cond
    if isinstance(alone, str) or alone.t_med <= 0:
        return NO_BASE
    return cond.t_med / alone.t_med


def lookup(values: dict[Key, Stat | None], key: Key) -> Stat | str:
    """The cell as a Stat, FAIL or `-`."""
    if key not in values:
        return NOT_RUN
    stat = values[key]
    return FAILED if stat is None else stat


def a2a_gbps(layout: str, size: int, t_med: float) -> float | None:
    """Per-rank remote bandwidth of an all-to-all in Gb/s; None when the layout is not known."""
    remote = REMOTE_RANKS.get(layout)
    if remote is None or t_med <= 0:
        return None
    return remote * size * 8 / t_med / 1e9


# --- run folders ---------------------------------------------------------------------------------


def parse_experiments(config: object) -> tuple[Experiment, ...]:
    """The experiments of a config.json with the victim apps (collected, with -msgsize) of each."""
    experiments = config.get("experiments") if isinstance(config, dict) else None
    if not isinstance(experiments, dict):
        return ()
    found = []
    for name, body in experiments.items():
        apps = body.get("apps") if isinstance(body, dict) else None
        if not isinstance(apps, dict):
            continue
        victims = []
        for key, app in apps.items():
            if not isinstance(app, dict) or not app.get("collect", True):
                continue
            match = SIZE_RE.search(str(app.get("args", "")))
            if match:
                victims.append((str(key), int(match.group(1))))
        found.append(Experiment(str(name), tuple(victims)))
    return tuple(found)


def guess_layout(folder: str) -> str:
    return next((t for t in folder.split("_") if t in LAYOUTS), "?")


def guess_victim(folder: str, config: object) -> str:
    """The victim from the folder name's tokens (a2a, ag, agtr, ardc), else from an app's path."""
    for token in folder.split("_"):
        if token in VICTIM_ALIASES:
            return VICTIM_ALIASES[token]
    text = json.dumps(config) if isinstance(config, dict) else ""
    for word, victim in (("alltoall", "a2a"), ("allgather", "agtr"), ("allreduce", "ardc")):
        if word in text:
            return victim
    return "?"


def read_json(path: Path) -> object:
    """The parsed file, or None when it is missing or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def text_field(data: object, name: str) -> str:
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def inspect_run(path: Path, meta: Meta | None) -> RunInfo:
    """Read the small files of a run folder. Raises RunError when it has no usable config.json."""
    config = read_json(path / "config.json")
    experiments = parse_experiments(config)
    if not experiments:
        raise RunError(f"{path.name}: no experiments in config.json")
    record = read_json(path / net_state.RECORD_FILE)
    row = read_json(path / "schedule_row.json")
    meta = meta or Meta(None, "", "", "", "", "OK", 0)
    state_candidates = [text_field(record, "label"), meta.state, text_field(row, "state")]
    state = next((s for s in state_candidates if s and s != "-"), UNKNOWN_STATE)
    layout = text_field(row, "layout") or meta.layout or guess_layout(path.name)
    victim = text_field(row, "victim") or meta.victim or guess_victim(path.name, config)
    return RunInfo(path, meta, state, layout, victim, experiments)


def load_points(info: RunInfo, only: set[str] | None = None) -> RunData:
    """Read the csvs: (condition, size) -> Stat, None where the csv of a finished run is missing.

    `only` limits the conditions read (the others are not looked at).
    """
    points: dict[tuple[str, int], Stat | None] = {}
    for experiment in info.experiments:
        if only is not None and experiment.name not in only:
            continue
        for key, size in experiment.apps:
            points[(experiment.name, size)] = read_stats(
                info.path / experiment.name / f"data_app_{key}.csv"
            )
    return RunData(info, points)


# --- cells ---------------------------------------------------------------------------------------


def build_cells(runs: list[RunData]) -> Cells:
    """Merge the attempts: per cell the newest successful one, else FAIL (None)."""
    values: dict[Key, Stat | None] = {}
    attempts: dict[Key, int] = {}
    for run in sorted(runs, key=lambda r: (r.info.meta.submitted, r.info.meta.order)):
        info = run.info
        for (condition, size), stat in run.points.items():
            key = (info.state, info.layout, info.victim, condition, size)
            attempts[key] = attempts.get(key, 0) + 1
            if stat is not None or key not in values:
                values[key] = stat
    return Cells(values, attempts)


def groups(cells: Cells) -> list[Group]:
    """One group per (state, layout, victim), in the order of the campaign."""
    found: dict[tuple[str, str, str], tuple[set[int], set[str]]] = {}
    for state, layout, victim, condition, size in cells.values:
        sizes, conditions = found.setdefault((state, layout, victim), (set(), set()))
        sizes.add(size)
        if condition != "alone":
            conditions.add(condition)
    names = list(CODES)

    def sort_key(item: tuple[str, str, str]) -> tuple:
        state, layout, victim = item
        return (order_of(state, STATE_LABELS), order_of(layout, LAYOUTS), order_of(victim, VICTIMS))

    return [
        Group(
            *item,
            tuple(sorted(found[item][0])),
            tuple(sorted(found[item][1], key=lambda c: order_of(c, names))),
        )
        for item in sorted(found, key=sort_key)
    ]


def analyse(cells: Cells) -> Analysis:
    """Every condition cell with a slowdown, in the order of the groups; the FAIL and n/a counts."""
    points: list[Point] = []
    fails: dict[str, int] = {}
    no_base = 0
    for group in groups(cells):
        for size in group.sizes:
            if lookup(cells.values, (group.state, group.layout, group.victim, "alone", size)) == FAILED:
                fails[group.state] = fails.get(group.state, 0) + 1
        for condition in group.conditions:
            for size in group.sizes:
                key = (group.state, group.layout, group.victim, condition, size)
                cond = lookup(cells.values, key)
                alone = lookup(cells.values, (*key[:3], "alone", size))
                result = slowdown(cond, alone)
                if isinstance(result, float):
                    assert isinstance(cond, Stat) and isinstance(alone, Stat)
                    points.append(Point(key, result, cond, alone))
                elif result == FAILED:
                    fails[group.state] = fails.get(group.state, 0) + 1
                elif result == NO_BASE:
                    no_base += 1
    return Analysis(points, fails, no_base)
