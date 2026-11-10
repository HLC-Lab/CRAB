#!/usr/bin/env python3
"""Plan the NPLB slot campaign: tiers, network states, block files, `schedule.tsv`, estimate.

The unit of work is a block file (`campaign.build_block`), independent of the network state: the
owner applies the state by hand and the run records it. The schedule lists "run this block under
this network state", in run order. The 8 network states are NPLB 0/1 x AR 0/1 x SR 0/1 (labels of
`net_state.label`); NPLB and AR are changed on the switches (the owner logs in and pastes, about
`--switch-min` minutes each time), SR is a NIC setting (`--sr-min`). A switch group is (nplb, ar),
in the order base (0,0), nplb (1,0), ar (0,1), nplb+ar (1,1); (1,1) is always last in a tier: it is
checked at the slot and, if the switch refuses it, its rows are dropped.

Tiers (`C3` = alone, steady_a128k, burst_l10ms_p10ms_a128k; `REST` = the other 8 conditions;
`S4` = 64 KiB, 1 MiB, 16 MiB, 256 MiB; `S4c` = the other four sizes; `KEY` = the states
nplb0_ar0_sr0 and nplb1_ar0_sr1), victims a2a, agtr, ardc:

    tier 1: block t1  (split-i, C3, S4)                        under all 8 states
    tier 2: blocks t2a (split-i, C3, S4c) and t2b (split-i, REST, all 8 sizes)   under KEY
    tier 3: block t3  (split-l and shared, C3, all 8 sizes)    under KEY
    tier 4: blocks t2a and t2b                                  under the 6 states that are not KEY

so no cell (state, layout, victim, condition, size) is measured twice and, per state, its tiers
together are the full grid. A cut-off keeps whole tiers. Files are `<block>_<layout>_<victim>.json`.

`--order tier` (default): rows by tier; inside a tier by state, then layout, victim and block part.
The first state of a tier is the one closest to where the previous row left the switches; then
always the cheapest remaining step (fewest switch changes, then SR changes, ties in canonical
order), the (1,1) states only when nothing else is left. `--order state`: one pass over the 8 states
with the same rule, all tiers of a state together: 4 switch changes at most, and a cut-off keeps
whole states. A switch change is two consecutive rows with a different (nplb, ar), the first row
counts as one (the start state is set by hand); an SR change likewise.

    campaign_plan.py [--out DIR] [--order tier|state] [--budget-hours H] [--estimate] [--local]
                     [--switch-min N] [--sr-min N]

It writes the block files and `schedule.tsv` to `--out` (default `<checkout>/local/campaign`, must
sit inside a `local/` directory) and prints the estimate report on stdout; `--estimate` writes
nothing. Output is deterministic (no timestamps). `--local`: the laptop rehearsal, one tier `L`, no
state, blocks `l` for split-i and shared. Steps narrate on stderr as `[HH:MM:SS] text`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign  # noqa: E402
import campaign_cost  # noqa: E402
import net_state  # noqa: E402
from campaign import UsageError  # noqa: E402
from gpu_set import KIB, MIB  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
State = tuple[int, int, int]  # (nplb, ar, sr)
GROUPS = ((0, 0), (1, 0), (0, 1), (1, 1))  # base, nplb, ar, nplb+ar
CANONICAL: list[State] = sorted(net_state.STATES, key=lambda s: (GROUPS.index((s[0], s[1])), s[2]))
KEY: tuple[State, ...] = ((0, 0, 0), (1, 0, 1))
OTHERS = tuple(s for s in CANONICAL if s not in KEY)
C3 = ("alone", "steady_a128k", "burst_l10ms_p10ms_a128k")
REST = tuple(c.name for c in campaign.CONDITIONS if c.name not in C3)
S4 = (64 * KIB, MIB, 16 * MIB, 256 * MIB)
S4C = tuple(s for s in campaign.SIZES if s not in S4)
VICTIMS = [v.stem for v in campaign.VICTIMS]
LAYOUTS = [lay.name for lay in campaign.LAYOUTS]
DEFAULT_OUT = CHECKOUT / "local" / "campaign"
HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)
UNMEASURED_NOTE = "unmeasured %: share of the estimated victim time on an assumption or a model"


@dataclass(frozen=True)
class Part:
    """A block: which layouts, conditions and sizes one block file holds."""

    name: str
    layouts: tuple[str, ...]
    conditions: tuple[str, ...]
    sizes: tuple[int, ...]


PARTS = {
    p.name: p
    for p in (
        Part("t1", ("split-i",), C3, S4),
        Part("t2a", ("split-i",), C3, S4C),
        Part("t2b", ("split-i",), REST, tuple(campaign.SIZES)),
        Part("t3", ("split-l", "shared"), C3, tuple(campaign.SIZES)),
    )
}
LOCAL_PART = Part(
    "l", ("split-i", "shared"), tuple(campaign.LOCAL_CONDITIONS), tuple(campaign.LOCAL_SIZES)
)


@dataclass(frozen=True)
class Tier:
    name: str
    states: tuple[State, ...]
    blocks: tuple[str, ...]


TIERS = (
    Tier("1", tuple(CANONICAL), ("t1",)),
    Tier("2", KEY, ("t2a", "t2b")),
    Tier("3", KEY, ("t3",)),
    Tier("4", OTHERS, ("t2a", "t2b")),
)
TIER_NAMES = [t.name for t in TIERS]


@dataclass(frozen=True)
class Job:
    """One run of a block under a network state (None: the laptop mode has no state)."""

    tier: str
    state: State | None
    block: str
    layout: str
    victim: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.block, self.layout, self.victim)

    @property
    def file(self) -> str:
        return f"{self.block}_{self.layout}_{self.victim}.json"


@dataclass(frozen=True)
class Cost:
    """Expected run of one block, seconds; `victim_s` of it is victim points, `unmeasured_s` of
    that rests on any source tag other than `measured`."""

    est_s: float
    apps: int
    victim_s: float
    unmeasured_s: float


@dataclass(frozen=True)
class Row:
    n: int
    job: Job
    cost: Cost
    switch_change: int
    sr_change: int
    own_s: float  # run time plus the switch and SR changes this row pays for
    cum_s: float

    @property
    def state(self) -> State | None:
        return self.job.state


# --- order and counting ------------------------------------------------------------------------


def step_cost(prev: State | None, cand: State) -> tuple[int, int]:
    """(switch change, SR change) from the state the last row left; the first row counts both."""
    if prev is None:
        return (1, 1)
    return (int(prev[:2] != cand[:2]), int(prev[2] != cand[2]))


def canonical_rank(state: State) -> tuple[int, int]:
    return (GROUPS.index((state[0], state[1])), state[2])


def order_states(states: Sequence[State], prev: State | None) -> list[State]:
    """Greedy order: cheapest step from `prev`, ties canonical, the (1,1) states last."""
    remaining = sorted(states, key=canonical_rank)
    ordered: list[State] = []
    while remaining:
        pool = [s for s in remaining if s[:2] != (1, 1)] or remaining
        if prev is None:  # nothing to compare with: the canonical first
            best = pool[0]
        else:
            best = min(pool, key=lambda s: (*step_cost(prev, s), canonical_rank(s)))
        ordered.append(best)
        remaining.remove(best)
        prev = best
    return ordered


def make_jobs(order: str, local: bool) -> list[Job]:
    """Every run of the campaign, in run order (`order` is `tier` or `state`)."""
    if local:
        return [Job("L", None, "l", lay, v) for lay in LOCAL_PART.layouts for v in VICTIMS]
    rank: dict[tuple[str, State], int] = {}
    prev: State | None = None
    for tier in TIERS:
        ordered = order_states(tier.states, prev)
        prev = ordered[-1]
        rank.update({(tier.name, s): i for i, s in enumerate(ordered)})
    overall = {s: i for i, s in enumerate(order_states(CANONICAL, None))}
    keyed: list[tuple[tuple[int, ...], Job]] = []
    for tier in TIERS:
        for state in tier.states:
            for block in tier.blocks:
                for layout in PARTS[block].layouts:
                    for victim in VICTIMS:
                        job = Job(tier.name, state, block, layout, victim)
                        where = [
                            LAYOUTS.index(layout),
                            VICTIMS.index(victim),
                            list(PARTS).index(block),
                        ]
                        tier_i = TIER_NAMES.index(tier.name)
                        if order == "tier":
                            key = (tier_i, rank[(tier.name, state)], *where)
                        else:
                            key = (overall[state], tier_i, *where)
                        keyed.append((tuple(key), job))
    return [job for _, job in sorted(keyed, key=lambda item: item[0])]


def changes(prev: State | None, state: State | None) -> tuple[int, int]:
    return (0, 0) if state is None else step_cost(prev, state)


def count_switch_changes(states: Sequence[State | None]) -> int:
    total, prev = 0, None
    for state in states:
        total += changes(prev, state)[0]
        prev = state if state is not None else prev
    return total


# --- cost --------------------------------------------------------------------------------------


def part_of(block: str) -> Part:
    return LOCAL_PART if block == "l" else PARTS[block]


def block_cost(key: tuple[str, str, str]) -> Cost:
    """Expected run of one block from the cost model, no walltime margin."""
    block, layout, victim = key
    part = part_of(block)
    contended = [campaign.find_conditions([c])[0].size is not None for c in part.conditions]
    est = campaign_cost.block_estimate(victim, layout, part.sizes, contended, local=block == "l")
    unmeasured = sum(v for tag, v in est.victim_s.items() if tag != "measured")
    return Cost(est.seconds, est.apps, sum(est.victim_s.values()), unmeasured)


def build_rows(
    jobs: Sequence[Job],
    costs: dict[tuple[str, str, str], Cost],
    switch_min: float,
    sr_min: float,
) -> list[Row]:
    """The schedule: switch and SR changes of every row and the running total in seconds."""
    rows: list[Row] = []
    prev: State | None = None
    cum = 0.0
    for n, job in enumerate(jobs, 1):
        cost = costs[job.key]
        switch, sr = changes(prev, job.state)
        own = cost.est_s + switch * switch_min * 60 + sr * sr_min * 60
        cum += own
        rows.append(Row(n, job, cost, switch, sr, own, cum))
        prev = job.state if job.state is not None else prev
    return rows


def tsv_lines(rows: Sequence[Row]) -> list[str]:
    lines = [HEADER]
    for r in rows:
        s = r.state
        state = ["-"] * 4 if s is None else [net_state.label(*s), str(s[0]), str(s[1]), str(s[2])]
        cells = [str(r.n), r.job.tier, *state, r.job.block, r.job.layout, r.job.victim]
        cells += [f"{r.cost.est_s / 60:.1f}", str(r.switch_change), str(r.sr_change)]
        cells += [f"{r.cum_s / 3600:.2f}", r.job.file]
        lines.append("\t".join(cells))
    return lines


# --- report ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TierStat:
    name: str
    blocks: int
    runs: int
    apps: int
    hours: float
    switch: int
    unmeasured: float  # percent of the victim time


def tier_stats(rows: Sequence[Row]) -> list[TierStat]:
    stats = []
    for name in dict.fromkeys(r.job.tier for r in rows):
        mine = [r for r in rows if r.job.tier == name]
        victim = sum(r.cost.victim_s for r in mine)
        unmeasured = sum(r.cost.unmeasured_s for r in mine)
        stats.append(
            TierStat(
                name,
                len({r.job.file for r in mine}),
                len(mine),
                sum(r.cost.apps for r in mine),
                sum(r.own_s for r in mine) / 3600,
                sum(r.switch_change for r in mine),
                100 * unmeasured / victim if victim else 0.0,
            )
        )
    return stats


def fit_text(rows: Sequence[Row], stats: Sequence[TierStat], order: str, budget: float) -> str:
    """What fits `budget` hours: whole tiers (whole states with `--order state`)."""
    if order == "state" and stats[0].name != "L":
        ends = [
            r.cum_s / 3600
            for i, r in enumerate(rows)
            if i + 1 == len(rows) or rows[i + 1].state != r.state
        ]
        what = "states"
    else:
        ends, total = [], 0.0
        for s in stats:
            total += s.hours
            ends.append(total)
        what = "tiers"
    fits = 0
    while fits < len(ends) and ends[fits] <= budget:
        fits += 1
    if fits == 0:
        return f"{what}: none fit {budget:g} h"
    if stats[0].name == "L":
        return f"tier L fits {budget:g} h"
    first = "1" if what == "states" else stats[0].name
    last = fits if what == "states" else stats[fits - 1].name
    return f"{what} {first}-{last} fit {budget:g} h"


def layout_lines(work: Path) -> list[str]:
    """One line per layout from `nplb topo`'s node_map.json, or the hint to run it."""
    path = work / "node_map.json"
    if not path.is_file():
        return ["layouts: run nplb topo first to see the leaf classes"]
    try:
        data = json.loads(path.read_text())
        layouts, when = data["layouts"], data["when"]
        lines = []
        for name in LAYOUTS:
            info = layouts.get(name)
            if info is not None:
                lines.append(
                    f"{name} victim pair {info['victim_pair']}, aggressor pair "
                    f"{info['aggressor_pair']} (map from {when})"
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["layouts: node_map.json unreadable: run nplb topo again"]
    return lines


def work_dir() -> Path:
    explicit = os.environ.get("WORK") or os.environ.get("NPLB_WORK")
    return Path(explicit) if explicit else CHECKOUT / "local" / "nplb-work"


def report(rows: Sequence[Row], args: argparse.Namespace) -> list[str]:
    stats = tier_stats(rows)
    total_h = rows[-1].cum_s / 3600
    switches = sum(r.switch_change for r in rows)
    other = "state" if args.order == "tier" else "tier"
    alt = count_switch_changes([j.state for j in make_jobs(other, args.local)])
    head = f"VERDICT: OK {total_h:.1f} h total ({len(rows)} runs, {switches} switch changes)"
    if args.budget_hours is not None:
        head += ", " + fit_text(rows, stats, args.order, args.budget_hours)
    lines = [head, ""]
    lines.append("tier  blocks  runs  apps  hours  switch changes  cum h  unmeasured %")
    cum = 0.0
    for s in stats:
        cum += s.hours
        lines.append(
            f"{s.name:<6}{s.blocks:>5}{s.runs:>6}{s.apps:>6}{s.hours:>7.1f}{s.switch:>12}"
            f"{cum:>12.1f}{s.unmeasured:>12.1f}"
        )
    victim = sum(r.cost.victim_s for r in rows)
    unmeasured = 100 * sum(r.cost.unmeasured_s for r in rows) / victim if victim else 0.0
    lines.append(
        f"{'total':<6}{len({r.job.file for r in rows}):>5}{len(rows):>6}"
        f"{sum(s.apps for s in stats):>6}{total_h:>7.1f}{switches:>12}{total_h:>12.1f}"
        f"{unmeasured:>12.1f}"
    )
    lines += [
        UNMEASURED_NOTE,
        f"overheads: {args.switch_min:g} min per switch change, {args.sr_min:g} min per SR change",
    ]
    if not args.local:
        f = campaign_cost
        lines += [
            "",
            "biggest assumptions (no measurement behind them):",
            f"  CONTENTION_FACTOR = {f.CONTENTION_FACTOR}: a victim against an aggressor takes "
            "that x its alone time",
            f"  ALLREDUCE_FACTOR = {f.ALLREDUCE_FACTOR}: allreduce takes that x the all-to-all "
            "time of the same size",
            "  shared = ppn8 proxy: the shared layout (4 ranks per node) reuses the 16-rank "
            "numbers",
        ]
    if not args.local:
        lines.append(
            f"order: {args.order} (--order {other} would need {alt} switch changes "
            f"instead of {switches})"
        )
        lines += layout_lines(work_dir())
    lines.append("nothing written (--estimate)" if args.estimate else f"files: {args.out}")
    return lines


# --- main --------------------------------------------------------------------------------------


def note(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)


def build_blocks(jobs: Sequence[Job], local: bool) -> dict[str, dict[str, Any]]:
    """File name -> config of every distinct block of the schedule."""
    files: dict[str, dict[str, Any]] = {}
    for job in jobs:
        if job.file in files:
            continue
        part = part_of(job.block)
        stem, config = campaign.build_block(
            job.layout,
            job.victim,
            sizes=list(part.sizes),
            conditions=list(part.conditions),
            local=local,
            prefix=part.name,
        )
        assert f"{stem}.json" == job.file, (stem, job.file)
        files[job.file] = config
    return files


def positive(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Plan the NPLB slot campaign: block files, schedule.tsv and an estimate."
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="folder for the files (default: "
        "<checkout>/local/campaign; must be inside a local/ directory)",
    )
    p.add_argument(
        "--order",
        choices=("tier", "state"),
        default="tier",
        help="tier (default): "
        "whole tiers in a cut-off; state: one pass over the 8 network states, the "
        "fewest switch changes",
    )
    p.add_argument(
        "--budget-hours",
        type=positive,
        default=None,
        help="say which whole tiers (states with --order state) fit this many hours",
    )
    p.add_argument("--estimate", action="store_true", help="write nothing, print the report only")
    p.add_argument("--local", action="store_true", help="laptop rehearsal: one tier L, no states")
    p.add_argument(
        "--switch-min",
        type=positive,
        default=10.0,
        help="minutes per switch change: the owner logs into the switches and pastes (default 10)",
    )
    p.add_argument("--sr-min", type=positive, default=2.0, help="minutes per SR change (default 2)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        campaign.check_out_dir(args.out)
        jobs = make_jobs(args.order, args.local)
        note(f"planning {len(jobs)} runs, order {args.order}")
        blocks = build_blocks(jobs, args.local)
        costs = {job.key: block_cost(job.key) for job in jobs}
        rows = build_rows(jobs, costs, args.switch_min, args.sr_min)
    except UsageError as exc:
        print(f"campaign_plan.py: error: {exc}", file=sys.stderr)
        return 2
    if not args.estimate:
        note(f"writing {len(blocks)} block files to {args.out}")
        args.out.mkdir(parents=True, exist_ok=True)
        for name, config in blocks.items():
            (args.out / name).write_text(json.dumps(config, indent=2) + "\n")
        note("writing schedule.tsv")
        (args.out / "schedule.tsv").write_text("\n".join(tsv_lines(rows)) + "\n", encoding="utf-8")
    print("\n".join(report(rows, args)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
