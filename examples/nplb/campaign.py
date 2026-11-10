#!/usr/bin/env python3
"""Write the CRAB configs ("blocks") of the NPLB contention campaign (stdlib only).

A block is one layout and one victim, `<layout>_<victim>.json`: one experiment per condition (11),
so one Slurm job. Layouts `split-i`, `split-l` and `shared`; victims `a2a` (`alltoall_comm_only`),
`agtr` (`allgather_comm_only`) and `ardc` (`allreduce_b`), buffers in GPU memory (`-gpu`). The 8
victim sizes (16 KiB to 256 MiB, x4) are chained inside each experiment, so the aggressor starts
once per experiment, 3 s before the chain, and stops (`end: f`) when the chain is done.

* `split-i`: allocation `interleaved`, partitions `victim` and `aggressor` of share 50: victim on
  nodes 0 and 2, aggressor on 1 and 3 (Slurm's order), 8 ranks per node each.
* `split-l`: the same with allocation `linear`: victim on nodes 0 and 1, aggressor on 2 and 3.
* `shared`: allocation `shared` (CRAB refuses it with partitions): both apps on all 4 nodes, 4
  ranks per node each, pinned apart as `gpu_set.py` pins: the victim on GPUs 0-3, ports `NICS[0:4]`
  and cores `CORES[0:4]`; the aggressor on GPUs 4-7 (`CUDA_VISIBLE_DEVICES=4,5,6,7`; with
  `CUDA_DEVICE_ORDER=PCI_BUS_ID` local rank i takes device 4 + i), `NICS[4:8]`, `CORES[4:8]`.

Conditions: `alone`; a steady aggressor (an all-to-all, `-endl -warmup 0`) at 128 KiB, 2 MiB and
16 MiB; bursts (`-blength`, `-bpause`) of 1, 10 and 100 ms at 50 % duty and of 10 ms at 25 % and
75 %, at 128 KiB; bursts of 10 ms at 50 % with 2 MiB and 16 MiB. The aggressor is never collected.

`-iter` aims at `campaign_cost.TARGET_S` per victim point, `-warmup` a tenth of it and
`-maxsamples` equals `-iter` (Blink keeps 1000 samples otherwise). Walltime: per experiment the
sum over its apps of 10 s (mpirun start-up) plus the estimated `(iter + warmup) x seconds per
iteration` of a victim app; summed, x1.5, `HH:MM:SS`, at least 30 min, refused above
`WALLTIME_CAP_S`. `timeout` (CRAB applies it to each experiment): the largest experiment x5, at
least 600 s.

    python3 campaign.py --out local/campaign                       # all 9 blocks
    python3 campaign.py --out local/campaign --layout shared --victim a2a
    python3 campaign.py --out local/campaign --sizes 1M,16M --conditions alone,steady_a2m
    python3 campaign.py --out local/campaign --local               # 2-node laptop blocks

`--local`: no `-gpu`, `binary`, `nics`, `cpus` or UCX variables, `-iter 5 -warmup 1`, sizes 1 KiB
and 1 MiB, layouts `split-i` and `shared`, conditions `alone`, `steady_a128k` and
`burst_l10ms_p10ms_a128k`; files `local_<layout>_<victim>.json`. `--out` must sit inside a
`local/` directory (git-ignored), where the files that hold node names go.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_cost  # noqa: E402
import gpu_set  # noqa: E402
from gpu_set import AGGRESSOR, CORES, GPU_ENV, KIB, MIB, NICS, Victim  # noqa: E402
from md_set import VICTIM_LEAD  # noqa: E402

WALLTIME_CAP_S = 6 * 3600  # the cluster's longest job
SHARED_OFFSET = 4  # in `shared` the aggressor takes the second half of the GPUs, ports and cores
LOCAL_RUN = (5, 1)  # (-iter, -warmup) of the laptop blocks
SIZES = [16 * KIB * 4**k for k in range(8)]  # 16 KiB .. 256 MiB
LOCAL_SIZES = [KIB, MIB]
LOCAL_CONDITIONS = ("alone", "steady_a128k", "burst_l10ms_p10ms_a128k")

VICTIMS = (
    Victim(
        "a2a",
        "alltoall_comm_only",
        "blink/alltoall/alltoall_comm_only.py",
        "all-to-all, communication only",
    ),
    Victim(
        "agtr",
        "allgather_comm_only",
        "blink/allgather/allgather_comm_only.py",
        "all-gather, communication only",
    ),
    Victim("ardc", "allreduce_b", "blink/allreduce/allreduce_b.py", "all-reduce"),
)


@dataclass(frozen=True)
class Layout:
    """How the 4 nodes are shared between the victim and the aggressor."""

    name: str
    mode: str  # CRAB allocation mode
    partitioned: bool  # `shared` cannot have partitions
    victim_ppn: int
    aggressor_ppn: int
    text: str  # where the apps run, for the descriptions


LAYOUTS = (
    Layout("split-i", "interleaved", True, 8, 8, "victim on nodes 0 and 2, aggressor on 1 and 3"),
    Layout("split-l", "linear", True, 8, 8, "victim on nodes 0 and 1, aggressor on 2 and 3"),
    Layout(
        "shared",
        "shared",
        False,
        4,
        4,
        "both apps on all four nodes, on separate GPUs, NIC ports and cores",
    ),
)


@dataclass(frozen=True)
class Condition:
    """One experiment of a block: no aggressor, a steady one or a bursty one."""

    name: str
    size: int | None  # aggressor message size in bytes; None: the victim runs alone
    burst: tuple[int, int] | None  # (burst length, pause) in ms; None: steady
    text: str


def _size_label(size: int) -> str:
    return f"{size // MIB}m" if size >= MIB else f"{size // KIB}k"


def _steady(size: int) -> Condition:
    return Condition(
        f"steady_a{_size_label(size)}",
        size,
        None,
        f"while a steady all-to-all of {size} bytes runs on the aggressor nodes "
        "(it starts first and stops at the end)",
    )


def _burst(length: int, pause: int, size: int) -> Condition:
    duty = round(100 * length / (length + pause))
    return Condition(
        f"burst_l{length}ms_p{pause}ms_a{_size_label(size)}",
        size,
        (length, pause),
        f"while a bursty all-to-all of {size} bytes (bursts of {length} ms, pauses of {pause} ms, "
        f"{duty} % duty) runs on the aggressor nodes (it starts first and stops at the end)",
    )


CONDITIONS = (
    Condition("alone", None, None, "alone: the reference for the runs with an aggressor"),
    _steady(128 * KIB),
    _steady(2 * MIB),
    _steady(16 * MIB),
    _burst(1, 1, 128 * KIB),
    _burst(10, 10, 128 * KIB),
    _burst(100, 100, 128 * KIB),
    _burst(10, 30, 128 * KIB),
    _burst(30, 10, 128 * KIB),
    _burst(10, 10, 2 * MIB),
    _burst(10, 10, 16 * MIB),
)


class UsageError(Exception):
    """A bad name or command line; the message is the one line printed on stderr."""


def _list(items: Sequence[Any]) -> list[str]:
    """The names of layouts, victims or conditions."""
    return [item.name if hasattr(item, "name") else item.stem for item in items]


def _names(items: Sequence[Any]) -> str:
    return ", ".join(_list(items))


def find_layout(name: str) -> Layout:
    for layout in LAYOUTS:
        if layout.name == name:
            return layout
    raise UsageError(f"unknown layout {name!r}: use {_names(LAYOUTS)}")


def find_victim(name: str) -> Victim:
    for victim in VICTIMS:
        if victim.stem == name:
            return victim
    raise UsageError(f"unknown victim {name!r}: use {_names(VICTIMS)}")


def find_conditions(names: Sequence[str]) -> list[Condition]:
    """The named conditions, in the campaign's order (the order of CONDITIONS)."""
    for name in names:
        if name not in [c.name for c in CONDITIONS]:
            raise UsageError(f"unknown condition {name!r}: use {_names(CONDITIONS)}")
    return [c for c in CONDITIONS if c.name in names]


def make_app(plan: Plan, program: Victim, args: str, ppn: int, **keys: Any) -> dict[str, Any]:
    """One app entry, built by gpu_set: the first `ppn` entries of NICS and CORES, GPU_ENV."""
    return gpu_set.make_app(plan.mode, program, args, ppn, **keys)


def pin_shared_aggressor(app: dict[str, Any], ppn: int) -> None:
    """Move the aggressor of the `shared` layout to GPUs, ports and cores 4..4+ppn-1."""
    low, high = SHARED_OFFSET, SHARED_OFFSET + ppn
    gpus = ",".join(str(i) for i in range(low, high))
    app["cpus"] = ",".join(str(c) for c in CORES[low:high])
    app["nics"] = list(NICS[low:high])
    app["env"] = {**GPU_ENV, "CUDA_VISIBLE_DEVICES": gpus}


def aggressor_args(condition: Condition) -> str:
    """Flags of the aggressor: steady, or bursty after `-grty 1`; no `-iter`, no `-maxsamples`."""
    args = f"-msgsize {condition.size} -endl -warmup 0 -grty 1"
    if condition.burst is not None:
        length, pause = condition.burst
        args += f" -blength {length}ms -bpause {pause}ms"
    return args


@dataclass
class Plan:
    """What one block is built from."""

    layout: Layout
    victim: Victim
    sizes: Sequence[int]
    local: bool

    @property
    def mode(self) -> gpu_set.Mode:
        return gpu_set.LOCAL if self.local else gpu_set.FULL


def victim_chain(
    plan: Plan, contended: bool, head_start: str
) -> tuple[dict[str, dict[str, Any]], float]:
    """The victim apps of one experiment, and their estimated seconds (start-up included)."""
    apps: dict[str, dict[str, Any]] = {}
    seconds = 0.0
    for key, size in enumerate(plan.sizes):
        seconds += campaign_cost.START_UP_S
        if plan.local:
            count, warmup = LOCAL_RUN
        else:
            name = plan.layout.name
            count, warmup, _ = campaign_cost.iterations(plan.victim.stem, size, name, contended)
            per_iteration, _ = campaign_cost.iter_time(plan.victim.stem, size, name, contended)
            seconds += (count + warmup) * per_iteration
        args = f"-msgsize {size} -iter {count} -warmup {warmup} -grty 1 -maxsamples {count}"
        apps[str(key)] = make_app(
            plan,
            plan.victim,
            args,
            plan.layout.victim_ppn,
            start=head_start if key == 0 else f"s{key - 1}",
            partition="victim" if key == 0 and plan.layout.partitioned else None,
        )
    return apps, seconds


def describe(plan: Plan, condition: Condition) -> str:
    """The plain-language description of one experiment."""
    span = f"{plan.sizes[0]} to {plan.sizes[-1]} bytes"
    where = "on two nodes" if plan.local else plan.layout.text
    return f"Victim ({plan.victim.text}) over a size sweep from {span}, {where}, {condition.text}."


def experiment(plan: Plan, condition: Condition) -> tuple[dict[str, Any], float]:
    """(experiment, estimated seconds): the victim chain, plus the aggressor unless `alone`."""
    contended = condition.size is not None
    apps, seconds = victim_chain(plan, contended, VICTIM_LEAD if contended else "0")
    if contended:
        ppn = plan.layout.aggressor_ppn
        aggressor = make_app(
            plan,
            AGGRESSOR,
            aggressor_args(condition),
            ppn,
            start="0",
            end="f",
            collect=False,
            partition="aggressor" if plan.layout.partitioned else None,
        )
        if not plan.layout.partitioned and not plan.local:
            pin_shared_aggressor(aggressor, ppn)
        apps[str(len(apps))] = aggressor
        seconds += campaign_cost.START_UP_S
    return {"description": describe(plan, condition), "apps": apps}, seconds


def allocation(layout: Layout) -> dict[str, Any]:
    """The block's allocation: partitions of share 50, or plain `shared` (no partitions)."""
    if not layout.partitioned:
        return {"mode": layout.mode}
    return {
        "mode": layout.mode,
        "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}},
    }


def local_layout(layout: Layout) -> Layout:
    """The 2-node laptop version: both apps with 2 ranks per node, or 1 each when `shared`."""
    ppn = 2 if layout.partitioned else 1
    return replace(layout, victim_ppn=ppn, aggressor_ppn=ppn)


def build_block(
    layout: str,
    victim: str,
    *,
    sizes: Sequence[int] | None = None,
    conditions: Sequence[Condition | str] | None = None,
    local: bool = False,
) -> tuple[str, dict[str, Any]]:
    """(file stem, config) of one block.

    `sizes` default to `SIZES` (1 KiB and 1 MiB when `local`), `conditions` (names or objects,
    kept in the given order) to `CONDITIONS` (`LOCAL_CONDITIONS` when `local`).

    Raises:
        UsageError: unknown name, a size without a cost, or a walltime over `WALLTIME_CAP_S`.
    """
    chosen = find_layout(layout)
    plan_victim = find_victim(victim)
    if local:
        chosen = local_layout(chosen)
    if sizes is None:
        sizes = LOCAL_SIZES if local else SIZES
    if conditions is None:
        conditions = LOCAL_CONDITIONS if local else CONDITIONS
    items = [c if isinstance(c, Condition) else find_conditions([c])[0] for c in conditions]
    plan = Plan(chosen, plan_victim, sizes, local)
    stem = f"{'local_' if local else ''}{chosen.name}_{plan_victim.stem}"
    experiments: dict[str, Any] = {}
    estimates: list[float] = []
    try:
        for condition in items:
            experiments[condition.name], seconds = experiment(plan, condition)
            estimates.append(seconds)
    except ValueError as exc:  # a size the cost model does not know
        raise UsageError(str(exc)) from exc
    walltime, timeout = "00:30:00", "600.0"
    if not local:
        needed = campaign_cost.walltime_needed(estimates)
        if needed > WALLTIME_CAP_S:
            raise UsageError(
                f"block {stem}: about {needed / 3600:.1f} hours of walltime, over the cap of "
                f"{WALLTIME_CAP_S / 3600:g} hours: use fewer sizes or conditions"
            )
        walltime = campaign_cost.hhmmss(max(campaign_cost.MIN_WALLTIME_S, needed))
        timeout = str(float(campaign_cost.timeout_s(estimates)))
    directives = [] if local else ["--exclusive"]
    directives.append(f"--job-name=nplb_{stem}")
    options: dict[str, Any] = {
        "name": f"nplb_{stem}",
        "numnodes": "2" if local else "4",
        "ppn": "2" if local else str(chosen.victim_ppn),
        "minruns": "1",
        "maxruns": "1",
        "timeout": timeout,
        "outformat": "csv",
        "walltime": walltime,
        "sbatch_directives": directives,
        "allocation": allocation(chosen),
    }
    config = {"schema_version": 2, "global_options": options, "experiments": experiments}
    return stem, config


def parse_sizes(text: str) -> list[int]:
    """`--sizes`: a comma list of byte counts, with K, M or G (powers of 1024) allowed."""
    sizes: set[int] = set()
    for word in text.split(","):
        word = word.strip()
        unit = {"K": KIB, "M": MIB, "G": 1 << 30}.get(word[-1:].upper(), 1)
        digits = word[:-1] if unit != 1 else word
        if not digits.isdigit() or int(digits) < 1 or int(digits) * unit % 4:
            raise UsageError(f"--sizes: {word!r} is not a positive multiple of 4 bytes")
        sizes.add(int(digits) * unit)
    return sorted(sizes)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Write the blocks of the NPLB contention campaign.")
    p.add_argument("--out", type=Path, required=True, help="directory for the block files")
    p.add_argument("--layout", default=None, help=f"comma list of {_names(LAYOUTS)} (default: all)")
    p.add_argument("--victim", default=None, help=f"comma list of {_names(VICTIMS)} (default: all)")
    p.add_argument("--sizes", default=None, help="victim sizes in bytes, K/M allowed: 16K,1M")
    p.add_argument(
        "--conditions", default=None, help="comma list of condition names (default: all)"
    )
    p.add_argument("--local", action="store_true", help="2-node laptop blocks, no GPUs")
    return p


def split(text: str | None, default: Sequence[str]) -> list[str]:
    return list(default) if text is None else [word.strip() for word in text.split(",")]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if "local" not in args.out.resolve().parts:
            raise UsageError(
                "--out must be inside a local/ directory (git-ignored): the campaign files "
                "that follow hold node names, and node names are never committed"
            )
        layouts = split(args.layout, ["split-i", "shared"] if args.local else _list(LAYOUTS))
        victims = split(args.victim, _list(VICTIMS))
        names = find_conditions(args.conditions.split(",")) if args.conditions else None
        sizes = parse_sizes(args.sizes) if args.sizes else None
        blocks = [
            build_block(lay, vic, sizes=sizes, conditions=names, local=args.local)
            for lay in layouts
            for vic in victims
        ]
    except UsageError as exc:
        print(f"campaign.py: error: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    for stem, config in blocks:
        (args.out / f"{stem}.json").write_text(json.dumps(config, indent=2) + "\n")
        count = sum(len(e["apps"]) for e in config["experiments"].values())
        print(f"  {stem}.json: {len(config['experiments'])} experiments, {count} apps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
