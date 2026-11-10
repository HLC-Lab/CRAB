#!/usr/bin/env python3
"""Plan the lab smoke test of the NPLB campaign: five small blocks and their `schedule.tsv`.

The smoke test runs before the real campaign and proves what no lab run has proved yet: the untested
commits of `src`, bursty aggressors with `-gpu`, `allreduce_b -gpu`, the `split-l` layout and the
`shared` placement on the H100s. Each feature is ONE block file `smoke_<feature>.json` (job name
`nplb_smoke_<feature>`), built by `campaign.build_block` with two victim sizes (64 KiB and 16 MiB)
and `-iter 20 -warmup 2 -maxsamples 20`:

    feature  layout   victim  conditions
    plain    split-i  a2a     alone, steady_a128k
    bursty   split-i  a2a     burst_l10ms_p10ms_a128k
    ardc     split-i  ardc    alone, steady_a128k
    split-l  split-l  a2a     alone, steady_a128k
    shared   shared   a2a     alone, steady_a128k

    smoke.py features
    smoke.py plan [FEATURE...] [--out DIR] [--estimate]

`plan` writes the blocks and a `schedule.tsv` in the format of `campaign_plan.py` (tier `S`, no
network state: `-` in the four state cells), one row per feature in table order, rows numbered from
1, so `nplb run N` can drive them. `--out` defaults to `<WORK>/smoke/campaign` (WORK is `$WORK`,
else `$NPLB_WORK`, else `<checkout>/local/nplb-work`): the smoke test keeps its own work folder, so
its row 1 is never mistaken for row 1 of the campaign. An explicit `--out` must sit inside a
`local/` directory. `--estimate` writes nothing. Output is deterministic (no timestamps); only
errors go to stderr. Exit 0, or 2 on a bad command line.

`est_min` of a row is the cost model's run time (10 s of mpirun start-up per app, 22 iterations x
seconds per iteration per victim point) plus `JOB_OVERHEAD_S`, a guess for the Slurm allocation and
CRAB's own start of one job.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign  # noqa: E402
import campaign_cost  # noqa: E402
import campaign_plan  # noqa: E402
from campaign import UsageError  # noqa: E402
from gpu_set import KIB, MIB  # noqa: E402

SIZES = [64 * KIB, 16 * MIB]
ITERS = (20, 2)  # (-iter and -maxsamples, -warmup) of every victim app
JOB_OVERHEAD_S = 60.0  # GUESS: Slurm allocation plus CRAB's start of one job
TIER = "S"


@dataclass(frozen=True)
class Feature:
    """One block of the smoke test."""

    name: str
    layout: str
    victim: str
    conditions: tuple[str, ...]
    proves: str

    @property
    def file(self) -> str:
        return f"smoke_{self.name}.json"


FEATURES = (
    Feature(
        "plain",
        "split-i",
        "a2a",
        ("alone", "steady_a128k"),
        "a plain victim: the untested commits of src",
    ),
    Feature(
        "bursty",
        "split-i",
        "a2a",
        ("burst_l10ms_p10ms_a128k",),
        "a bursty aggressor with -gpu",
    ),
    Feature(
        "ardc",
        "split-i",
        "ardc",
        ("alone", "steady_a128k"),
        "allreduce_b with -gpu",
    ),
    Feature(
        "split-l",
        "split-l",
        "a2a",
        ("alone", "steady_a128k"),
        "the linear layout: victim on nodes 0 and 1",
    ),
    Feature(
        "shared",
        "shared",
        "a2a",
        ("alone", "steady_a128k"),
        "the shared placement: both apps on all 4 nodes",
    ),
)


@dataclass(frozen=True)
class Cost:
    """Expected run of one block, seconds; `unmeasured_s` of `victim_s` rests on an assumption."""

    run_s: float  # the cost model: start-up of every app plus the victim iterations
    apps: int
    victim_s: float  # the iterations of the victim points
    unmeasured_s: float  # the part of victim_s whose source tag is not `measured`


@dataclass(frozen=True)
class Row:
    n: int
    feature: Feature
    cost: Cost
    cum_s: float  # running total, job overhead included

    @property
    def est_s(self) -> float:
        return self.cost.run_s + JOB_OVERHEAD_S


def default_out(env: Mapping[str, str]) -> Path:
    """`<WORK>/smoke/campaign`: WORK is `$WORK`, else `$NPLB_WORK`, else `<checkout>/local/nplb-work`."""
    explicit = env.get("WORK") or env.get("NPLB_WORK")
    work = Path(explicit) if explicit else campaign_plan.CHECKOUT / "local" / "nplb-work"
    return work / "smoke" / "campaign"


def select(names: Sequence[str]) -> list[Feature]:
    """The named features in table order (all of them when `names` is empty)."""
    valid = [f.name for f in FEATURES]
    for name in names:
        if name not in valid:
            raise UsageError(f"unknown feature {name!r}: use {', '.join(valid)}")
    return [f for f in FEATURES if not names or f.name in names]


def build_block(feature: Feature) -> dict[str, Any]:
    """The config of one feature: `campaign.build_block`, renamed so that the job name is unique."""
    stem, config = campaign.build_block(
        feature.layout,
        feature.victim,
        sizes=SIZES,
        conditions=list(feature.conditions),
        prefix="smoke",
        iters=ITERS,
    )
    old, new = f"nplb_{stem}", f"nplb_smoke_{feature.name}"  # plain and bursty share a stem
    options = config["global_options"]
    options["name"] = new
    options["sbatch_directives"] = [d.replace(old, new) for d in options["sbatch_directives"]]
    return config


def block_cost(feature: Feature) -> Cost:
    """Expected run of one block from `campaign_cost`, with the fixed iteration count."""
    count, warmup = ITERS
    seconds, apps, victim_s, unmeasured_s = 0.0, 0, 0.0, 0.0
    for name in feature.conditions:
        contended = campaign.find_conditions([name])[0].size is not None
        for size in SIZES:
            per_iteration, source = campaign_cost.iter_time(
                feature.victim, size, feature.layout, contended
            )
            point = (count + warmup) * per_iteration
            seconds += campaign_cost.START_UP_S + point
            apps += 1
            victim_s += point
            if source != "measured":
                unmeasured_s += point
        if contended:  # the aggressor is one more app
            seconds += campaign_cost.START_UP_S
            apps += 1
    return Cost(seconds, apps, victim_s, unmeasured_s)


def build_rows(features: Sequence[Feature]) -> list[Row]:
    rows: list[Row] = []
    cum = 0.0
    for n, feature in enumerate(features, 1):
        cost = block_cost(feature)
        cum += cost.run_s + JOB_OVERHEAD_S
        rows.append(Row(n, feature, cost, cum))
    return rows


def tsv_lines(rows: Sequence[Row]) -> list[str]:
    """The lines of schedule.tsv, in the cell order of `campaign_plan.tsv_lines`."""
    lines = [campaign_plan.HEADER]
    for r in rows:
        f = r.feature
        cells = [str(r.n), TIER, "-", "-", "-", "-", f.name, f.layout, f.victim]
        cells += [f"{r.est_s / 60:.1f}", "0", "0", f"{r.cum_s / 3600:.2f}", f.file]
        lines.append("\t".join(cells))
    return lines


def report(rows: Sequence[Row], out: Path, estimate: bool) -> list[str]:
    total_s = rows[-1].cum_s
    model_s = sum(r.cost.run_s for r in rows)
    victim_s = sum(r.cost.victim_s for r in rows)
    unmeasured_s = sum(r.cost.unmeasured_s for r in rows)
    lines = [
        f"VERDICT: OK about {total_s / 60:.1f} min for {len(rows)} features "
        f"({model_s / 60:.1f} min cost model + {len(rows) * JOB_OVERHEAD_S / 60:.1f} min job overhead)",
        "",
        "feature  layout   victim  conditions  apps  est_min",
    ]
    for r in rows:
        f = r.feature
        lines.append(
            f"{f.name:<9}{f.layout:<9}{f.victim:<8}{len(f.conditions):>10}{r.cost.apps:>6}"
            f"{r.est_s / 60:>9.1f}"
        )
    lines.append(
        f"{'total':<9}{'':<9}{'':<8}{'':>10}{sum(r.cost.apps for r in rows):>6}{total_s / 60:>9.1f}"
    )
    lines += [
        f"cost model: {campaign_cost.START_UP_S:g} s of mpirun start-up per app and "
        f"{ITERS[0] + ITERS[1]} iterations x seconds per iteration per victim point; the "
        f"iterations are {victim_s:.1f} s of the {model_s:.0f} s, {unmeasured_s:.1f} s of them on "
        "an assumption (allreduce model, shared proxy, contention factor)",
        f"guess: {JOB_OVERHEAD_S:g} s per job for the Slurm allocation and CRAB's own start, "
        "no measurement behind it; est_min includes it",
        "nothing written (--estimate)" if estimate else f"files: {out}",
    ]
    if not estimate:
        lines.append("next: nplb smoke")
    return lines


def features_table() -> list[str]:
    return [f"{f.name:<9}{f.layout:<9}{f.victim:<7}{f.proves}" for f in FEATURES]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Plan the lab smoke test of the NPLB campaign.")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("features", help="print the feature table")
    plan = commands.add_parser("plan", help="write the blocks and schedule.tsv")
    plan.add_argument("features", nargs="*", help="features to plan (default: all)")
    plan.add_argument(
        "--out",
        type=Path,
        default=None,
        help="folder for the files (default: <WORK>/smoke/campaign; an explicit one must be "
        "inside a local/ directory)",
    )
    plan.add_argument("--estimate", action="store_true", help="write nothing, print the report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "features":
        print("\n".join(features_table()))
        return 0
    try:
        if args.out is not None:
            campaign.check_out_dir(args.out)
        out = args.out if args.out is not None else default_out(os.environ)
        features = select(args.features)
        blocks = {f.file: build_block(f) for f in features}
        rows = build_rows(features)
    except UsageError as exc:
        print(f"smoke.py: error: {exc}", file=sys.stderr)
        return 2
    if not args.estimate:
        out.mkdir(parents=True, exist_ok=True)
        for name, config in blocks.items():
            (out / name).write_text(json.dumps(config, indent=2) + "\n")
        (out / "schedule.tsv").write_text("\n".join(tsv_lines(rows)) + "\n", encoding="utf-8")
    print("\n".join(report(rows, out, args.estimate)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
