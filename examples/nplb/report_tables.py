"""The pages of `nplb report` and `nplb compare`: pure rendering to lists of lines (stdlib only).

Nothing here reads a file. `summary_lines` is page 1 of `report` (verdict first), `group_table` one
table per state, layout and victim (rows = victim message sizes, columns = `alone ms` and the
conditions), `compare_lines` one victim and condition across the network states, `paginate` joins
the blocks with form feeds so that no table is cut by the pager. A table too wide for the page is
split into column groups that each repeat the fixed columns; a row is never cut. Python 3.10.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass

import report_data as data
from report_data import Analysis, Cells, Group, Stat

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB
GAP = "  "
CODE_LEGEND = (
    "conditions (column codes):",
    "  alone  no aggressor: the baseline every slowdown divides by",
    "  s128k s2m s16m   steady aggressor, messages of 128 KiB, 2 MiB, 16 MiB",
    "  b1/1 b10/10 b100/100 b10/30 b30/10   bursts: length/pause in ms, aggressor 128 KiB",
    "  b10/10@2m b10/10@16m   bursts of 10/10 ms, aggressor 2 MiB, 16 MiB",
)
CELL_LEGEND = (
    "cells: x1.52 = median / alone median (same state, layout, victim, size); FAIL = the app failed;",
    "  n/a = no alone to divide by; - = not run. alone ms = median of the slowest rank, in ms.",
    "Gb/s/rank (a2a, remote bytes only): the alone column of the a2a tables; agtr and ardc have none.",
)
STATE_LEGEND = "n=NPLB a=AR s=SR, 1 = on, 0 = off (n1a0s1 = nplb1_ar0_sr1)"


@dataclass(frozen=True)
class Column:
    header: str
    cells: list[str]
    left: bool = False  # left-aligned (the size column); the others are right-aligned


# --- number formats ------------------------------------------------------------------------------


def size_label(size: int) -> str:
    """16384 -> 16KiB, 268435456 -> 256MiB; a size that is no whole KiB: bytes."""
    for unit, name in ((GIB, "GiB"), (MIB, "MiB"), (KIB, "KiB")):
        if size >= unit and size % unit == 0:
            return f"{size // unit}{name}"
    return f"{size}B"


def sig3(value: float) -> str:
    """Three significant digits without an exponent: 0.136, 12.7, 123, 1234."""
    if value <= 0 or not math.isfinite(value):
        return "0"
    rounded = float(f"{value:.3g}")
    decimals = max(0, 2 - math.floor(math.log10(rounded)))
    return f"{rounded:.{decimals}f}"


def ms_text(stat: Stat | str) -> str:
    return stat if isinstance(stat, str) else sig3(stat.t_med * 1000)


def slow_text(value: float | str) -> str:
    return value if isinstance(value, str) else f"x{value:.2f}"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


# --- columns -------------------------------------------------------------------------------------


def _line(columns: list[Column], widths: list[int], row: int | None) -> str:
    parts = []
    for column, width in zip(columns, widths, strict=True):
        text = column.header if row is None else column.cells[row]
        parts.append(text.ljust(width) if column.left else text.rjust(width))
    return GAP.join(parts).rstrip()


def render_columns(fixed: list[Column], varying: list[Column], width: int) -> list[str]:
    """Header and rows; the varying columns go in groups as wide as `width`, the fixed ones repeat."""
    rows = len(fixed[0].cells)

    def wide(column: Column) -> int:
        return max([len(column.header), *(len(cell) for cell in column.cells)])

    fixed_widths = [wide(c) for c in fixed]
    base = sum(fixed_widths) + len(GAP) * (len(fixed) - 1)
    batches: list[list[Column]] = [[]]
    used = base
    for column in varying:
        need = len(GAP) + wide(column)
        if batches[-1] and used + need > width:
            batches.append([])
            used = base
        batches[-1].append(column)
        used += need
    lines: list[str] = []
    for batch in batches:
        columns = [*fixed, *batch]
        widths = [*fixed_widths, *(wide(c) for c in batch)]
        lines.append(_line(columns, widths, None))
        lines += [_line(columns, widths, row) for row in range(rows)]
    return lines


# --- victim tables -------------------------------------------------------------------------------


def group_table(group: Group, cells: Cells, analysis: Analysis, width: int) -> list[str]:
    """The table of one state, layout and victim."""
    points = sum(1 for p in analysis.points if p.key[:3] == (group.state, group.layout, group.victim))
    prefix = (group.state, group.layout, group.victim)
    alone = [data.lookup(cells.values, (*prefix, "alone", size)) for size in group.sizes]
    fixed = [
        Column("size", [size_label(s) for s in group.sizes], left=True),
        Column("alone ms", [ms_text(a) for a in alone]),
    ]
    if group.victim == "a2a":
        gbps = [
            data.a2a_gbps(group.layout, size, a.t_med) if isinstance(a, Stat) else None
            for size, a in zip(group.sizes, alone, strict=True)
        ]
        fixed.append(Column("Gb/s", ["-" if g is None else sig3(g) for g in gbps]))
    varying = []
    for condition in group.conditions:
        texts = []
        for size, base in zip(group.sizes, alone, strict=True):
            cond = data.lookup(cells.values, (*prefix, condition, size))
            texts.append(slow_text(data.slowdown(cond, base)))
        varying.append(Column(data.cond_code(condition), texts))
    title = f"== {group.state} | {group.layout} | {group.victim}  ({plural(points, 'point')})"
    return [title, *render_columns(fixed, varying, width)]


# --- page 1 --------------------------------------------------------------------------------------


def verdict_line(label: str, analysis: Analysis) -> str:
    """The first line of `report`: OK with the worst slowdown, WARN with FAILs or nothing to show."""
    fails = analysis.fails
    if not analysis.points:
        if fails:
            return f"VERDICT: WARN {label}: 0 points, {fails} FAIL"
        return f"VERDICT: WARN {label}: no slowdown points yet (needs an alone run and an aggressor run)"
    worst = max(analysis.points, key=lambda p: p.value)
    _state, _layout, victim, condition, size = worst.key
    head = f"{label}: {len(analysis.points)} points, worst slowdown x{worst.value:.2f}"
    head += f" ({victim} {condition} {size_label(size)})"
    return f"VERDICT: WARN {head}, {fails} FAIL" if fails else f"VERDICT: OK {head}"


def summary_lines(
    scope: str,
    states: list[str],
    cells: Cells,
    analysis: Analysis,
    others: list[tuple[str, int]],
    problems: list[str],
) -> list[str]:
    """Page 1: verdict, scope, the states with their points, worst per victim, legends, notes."""
    label = states[0] if len(states) == 1 else plural(len(states), "state")
    lines = [verdict_line(label, analysis), f"scope: {scope}"]
    lines.append("states with data (points = cells with a slowdown):")
    for state in states:
        count = sum(1 for p in analysis.points if p.key[0] == state)
        fails = analysis.fails_by_state.get(state, 0)
        lines.append(f"  {state:<15}{data.state_abbrev(state):<8}{plural(count, 'point'):<11}{fails} FAIL")
    if others:
        text = "also in the run log: " + ", ".join(f"{s} ({plural(n, 'run')})" for s, n in others)
        wrapped = textwrap.wrap(text + "  -> nplb report --state LABEL", 98, subsequent_indent="  ")
        lines += wrapped[:2]
    lines.append("worst slowdown per victim (code = condition, med/p99 = this condition, alone):")
    for victim in data.VICTIMS:
        mine = [p for p in analysis.points if p.key[2] == victim]
        if mine:
            lines.append(worst_line(max(mine, key=lambda p: p.value), len(states) > 1))
    lines += list(CODE_LEGEND)
    lines += list(CELL_LEGEND)
    if cells.repeated:
        lines.append(f"{cells.repeated} cells had more than one attempt (newest successful used)")
    lines += problems[:3]
    lines.append("next: nplb page 2 shows one table per state, layout and victim")
    return lines


def worst_line(point: data.Point, with_state: bool) -> str:
    state, layout, victim, condition, size = point.key
    where = f" {data.state_abbrev(state)}" if with_state else ""
    text = f"  {victim:<5}x{point.value:.2f} {data.cond_code(condition)} {layout} {size_label(size)}{where}"
    return f"{text}  med {ms_text(point.cond)} ms  p99 {sig3(point.cond.t_p99 * 1000)} ms  alone {ms_text(point.alone)} ms"


# --- compare -------------------------------------------------------------------------------------


def compare_lines(
    victim: str,
    condition: str,
    layout: str,
    cells: Cells,
    states: list[str],
    width: int,
) -> list[str]:
    """One victim and condition: rows = sizes, columns = the network states."""
    head = f"compare {victim} {condition} {layout}"
    keys = [k for k in cells.values if k[1:4] == (layout, victim, condition)]
    sizes = sorted({k[4] for k in keys})
    columns = list(data.STATE_LABELS)
    columns += sorted(s for s in states if s not in data.STATE_LABELS)
    have = {k[0] for k in keys if isinstance(cells.values[k], Stat)}
    if not have:
        return [
            f"VERDICT: WARN {head}: no state has data",
            "no finished run of this victim and layout measured this condition",
        ]
    alone_only = condition == "alone"
    lines = [f"VERDICT: OK {head}: {len(have)} of {len(columns)} states have data"]
    lines.append("cell = alone median in ms" if alone_only else "cell = median / alone median of the state")
    varying = []
    numbers: list[tuple[float, str, int]] = []
    for state in columns:
        texts = []
        for size in sizes:
            cond = data.lookup(cells.values, (state, layout, victim, condition, size))
            if alone_only:
                texts.append(ms_text(cond))
                continue
            base = data.lookup(cells.values, (state, layout, victim, "alone", size))
            result = data.slowdown(cond, base)
            texts.append(slow_text(result))
            if isinstance(result, float):
                numbers.append((result, state, size))
        varying.append(Column(data.state_abbrev(state), texts))
    fixed = [Column("size", [size_label(s) for s in sizes], left=True)]
    lines += render_columns(fixed, varying, width)
    lines.append(STATE_LEGEND)
    if cells.repeated:
        lines.append(f"{cells.repeated} cells had more than one attempt (newest successful used)")
    if numbers:
        worst = max(numbers, key=lambda item: item[0])
        best = min(numbers, key=lambda item: item[0])
        lines.append(
            f"worst: {worst[1]} at {size_label(worst[2])} x{worst[0]:.2f}; "
            f"best: {best[1]} at {size_label(best[2])} x{best[0]:.2f}"
        )
    return lines


# --- pages ---------------------------------------------------------------------------------------


def paginate(first: list[str], blocks: list[list[str]], capacity: int) -> list[str]:
    """`first` is page 1; the blocks follow, several to a page, never split across a page break.

    A line that is exactly a form feed starts a new page in the pager.
    """
    out = list(first)
    used = 0
    for block in blocks:
        if used == 0:
            out.append("\f")
        elif used + 1 + len(block) > capacity:
            out.append("\f")
            used = 0
        elif used:
            out.append("")
            used += 1
        out += block
        used += len(block)
    return out
