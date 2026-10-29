#!/usr/bin/env python3
"""Write the GPU comm-only congestion suites of the NPLB lab round (stdlib only).

Two files, one per victim: `gpu_a2a_comm.json` (victim `alltoall_comm_only`) and
`gpu_ag_comm.json` (victim `allgather_comm_only`). Each has 8 experiments, `ppn{1,2,4,8}_alone`
and `ppn{1,2,4,8}_vs_a2a`, on 4 H100 nodes with the buffers in GPU memory (`-gpu`): the victim
runs on Slurm nodes 0 and 2, a steady all-to-all aggressor on nodes 1 and 3 (interleaved
allocation, 50/50). CRAB has no sweep expansion, so each experiment is a chain of apps, one per
message size (1 KiB to 512 MiB, x4), each starting when the previous one ends. The ranks of a
node use the first `ppn` entries of the ppn-8 layout: local rank i on core CORES[i], port
NICS[i] and GPU i (Blink's `-gpu` picks the GPU by local rank, and `CUDA_DEVICE_ORDER=PCI_BUS_ID`
fixes the device order).

The aggressor is `alltoall_comm_only -endl` without `-blength` (steady), `collect: false`, and
starts `VICTIM_LEAD` seconds before the victim chain. `--aggr-ppn` (1, 2, 4, 8 or `same`, the
default: the victim's ppn) and `--aggr-msgsize` (bytes, default 2 MiB) take comma lists; with
more than one (ppn, size) pair every victim ppn gets one `vs` experiment per pair, named
`ppn{p}_vs_a2a_ap{aggressor ppn}_as{size}`.

    python3 gpu_set.py --out h100/                 # the two lab files
    python3 gpu_set.py --out h100/ --smoke         # ppn 1 and 8, two sizes, -iter 20
    python3 gpu_set.py --out local/ --local        # laptop files: see below

`--local` is only for driving the flow (generate, check, launch, parse) on a laptop without GPUs
or InfiniBand: ppn 1 and 2, sizes 1 KiB and 1 MiB, `-iter 5 -warmup 1`, no `-gpu`, `binary`,
`nics`, `cpus` or UCX variables. The wrappers and the 4-node interleaved layout stay the same.
It cannot be combined with `--smoke`. Node names are never written into a config.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_configs import LAYOUTS, Placement, Settings  # noqa: E402
from md_set import VICTIM_LEAD  # noqa: E402

KIB = 1 << 10
MIB = 1 << 20
SIZES = [KIB * 4**k for k in range(10)] + [512 * MIB]  # 1 KiB .. 256 MiB (x4), then 512 MiB
SMOKE_SIZES = [MIB, 64 * MIB]
LOCAL_SIZES = [KIB, MIB]
PPNS = (1, 2, 4, 8)
SMOKE_PPNS = (1, 8)
LOCAL_PPNS = (1, 2)
CORES = (0, 1, 2, 3, 40, 41, 42, 43)  # core of local rank i
NICS = (  # port of local rank i; GPU i sits behind the same PCIe switch
    "mlx5_0:1",
    "mlx5_2:1",
    "mlx5_4:1",
    "mlx5_8:1",
    "mlx5_10:1",
    "mlx5_12:1",
    "mlx5_14:1",
    "mlx5_16:1",
)
BIN_DIR = "~/crab/lab/blink-gpu/build-cuda/bin"
GPU_ENV = {
    "UCX_MAX_RNDV_RAILS": "1",
    "UCX_IB_ROCE_REACHABILITY_MODE": "all",
    "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
}
BIG_MESSAGE = 32 * MIB  # from this size on, fewer iterations
AGGRESSOR_SIZE = 2 * MIB
AGGRESSOR_PPN_CHOICES = ("1", "2", "4", "8")
SMOKE_RUN = (20, 5)  # (-iter, -warmup)
LOCAL_RUN = (5, 1)


@dataclass(frozen=True)
class Victim:
    """One suite: the victim program, its wrapper and the file stem."""

    stem: str
    program: str
    wrapper: str
    text: str  # what the victim does, for the descriptions


VICTIMS = (
    Victim(
        "gpu_a2a_comm",
        "alltoall_comm_only",
        "blink/alltoall/alltoall_comm_only.py",
        "all-to-all, communication only",
    ),
    Victim(
        "gpu_ag_comm",
        "allgather_comm_only",
        "blink/allgather/allgather_comm_only.py",
        "all-gather, communication only",
    ),
)
AGGRESSOR = VICTIMS[0]  # the aggressor is always the all-to-all


@dataclass(frozen=True)
class Mode:
    """What differs between the lab files, the smoke files and the laptop files."""

    prefix: str
    ppns: tuple[int, ...]
    sizes: list[int]
    gpu: bool  # `-gpu`, `binary`, `nics`, `cpus` and the UCX/CUDA variables
    walltime: str
    timeout: str
    exclusive: bool
    small: tuple[int, int]  # (`-iter`, `-warmup`) below BIG_MESSAGE
    big: tuple[int, int]  # the same from BIG_MESSAGE up

    def iterations(self, size: int) -> tuple[int, int]:
        """(`-iter`, `-warmup`) of one message size."""
        return self.small if size < BIG_MESSAGE else self.big


# Walltime of a lab file: 8 experiments x 11 sizes = 88 apps, each costing about 12 s of mpirun
# start-up (18 min in all) plus its runs, allowed 60 s on average (88 min); the sum, about 2 h,
# is tripled as slack for the slowest sizes under congestion.
FULL = Mode("", PPNS, SIZES, True, "06:00:00", "3600.0", True, (1000, 100), (100, 50))
SMOKE = Mode(
    "smoke_", SMOKE_PPNS, SMOKE_SIZES, True, "00:30:00", "600.0", True, SMOKE_RUN, SMOKE_RUN
)
LOCAL = Mode(
    "local_", LOCAL_PPNS, LOCAL_SIZES, False, "00:30:00", "600.0", False, LOCAL_RUN, LOCAL_RUN
)


def where(mode: Mode, ppn: int) -> Placement:
    """The placement of `ppn` ranks per node: the first `ppn` entries of the H100 layout."""
    if not mode.gpu:
        return Placement(ppn, (), "")
    return Placement(ppn, NICS[:ppn], ",".join(str(c) for c in CORES[:ppn]))


def make_app(
    mode: Mode,
    program: Victim,
    args: str,
    ppn: int,
    *,
    start: str,
    end: str = "",
    collect: bool = True,
    partition: str | None = None,
) -> dict[str, Any]:
    """One app: the wrapper, the placement of `ppn` ranks, and in lab mode the GPU binary and env."""
    entry = Settings.app(
        program.wrapper,
        f"{args} -gpu" if mode.gpu else args,
        where(mode, ppn),
        1,
        start=start,
        end=end,
        collect=collect,
        partition=partition,
        env=GPU_ENV if mode.gpu else None,
        ucx_env=False,
    )
    if not mode.gpu:
        entry.pop("cpus", None)  # the laptop files bind no cores
        return entry
    # `binary` right after `path`, as a reader expects it
    return {"path": entry.pop("path"), "binary": f"{BIN_DIR}/{program.program}", **entry}


def victim_chain(
    mode: Mode, victim: Victim, ppn: int, head_start: str
) -> dict[str, dict[str, Any]]:
    """The victim apps of one experiment: one per size, each starting after the previous one.

    Chained apps carry the head's `nics` list too (the existing H100 configs do the same).
    """
    apps: dict[str, dict[str, Any]] = {}
    for key, size in enumerate(mode.sizes):
        iterations, warmup = mode.iterations(size)
        args = f"-msgsize {size} -iter {iterations} -warmup {warmup} -grty 1"
        apps[str(key)] = make_app(
            mode,
            victim,
            args,
            ppn,
            start=head_start if key == 0 else f"s{key - 1}",
            partition="victim" if key == 0 else None,
        )
    return apps


def aggressor_app(mode: Mode, ppn: int, size: int) -> dict[str, Any]:
    """The steady all-to-all: starts first, is never collected, stops when the victim is done."""
    return make_app(
        mode,
        AGGRESSOR,
        f"-msgsize {size} -endl -grty 1",
        ppn,
        start="0",
        end="f",
        collect=False,
        partition="aggressor",
    )


def aggressor_pairs(victim_ppn: int, ppns: list[str], sizes: list[int]) -> list[tuple[int, int]]:
    """The (aggressor ppn, size) pairs of one victim ppn, without repeats, ppn-major."""
    pairs: list[tuple[int, int]] = []
    for word in ppns:
        pair_ppn = victim_ppn if word == "same" else int(word)
        for size in sizes:
            if (pair_ppn, size) not in pairs:
                pairs.append((pair_ppn, size))
    return pairs


def experiments(
    mode: Mode, victim: Victim, ppns: list[str], sizes: list[int]
) -> dict[str, dict[str, Any]]:
    """The experiments of one file: per victim ppn, `alone` and one `vs` per aggressor pair."""
    found: dict[str, dict[str, Any]] = {}
    span = f"{mode.sizes[0]} to {mode.sizes[-1]} bytes"
    for ppn in mode.ppns:
        base = f"Victim ({victim.text}) over a size sweep from {span} on the victim nodes"
        found[f"ppn{ppn}_alone"] = {
            "description": f"{base}, ppn {ppn}, alone: the reference for the runs "
            "with an aggressor.",
            "apps": victim_chain(mode, victim, ppn, "0"),
        }
        pairs = aggressor_pairs(ppn, ppns, sizes)
        for pair_ppn, size in pairs:
            name = f"ppn{ppn}_vs_a2a"
            if len(pairs) > 1:
                name += f"_ap{pair_ppn}_as{size}"
            apps = victim_chain(mode, victim, ppn, VICTIM_LEAD)
            apps[str(len(apps))] = aggressor_app(mode, pair_ppn, size)
            found[name] = {
                "description": f"{base}, ppn {ppn}, while a steady all-to-all of "
                f"{size} bytes (ppn {pair_ppn}) runs on the aggressor nodes (it "
                "starts first and stops at the end).",
                "apps": apps,
            }
    return found


def build(
    mode: Mode, victim: Victim, ppns: list[str], sizes: list[int]
) -> tuple[str, dict[str, Any]]:
    """(file stem, config) of one suite."""
    stem = f"{mode.prefix}{victim.stem}"
    layout = LAYOUTS[4]
    directives = ["--exclusive"] if mode.exclusive else []
    options: dict[str, Any] = {
        "name": stem,
        "numnodes": str(layout.nodes),
        "ppn": "8",
        "minruns": "1",
        "maxruns": "1",
        "timeout": mode.timeout,
        "outformat": "csv",
        "walltime": mode.walltime,
        "sbatch_directives": [*directives, f"--job-name=nplb_{stem}"],
        "allocation": {
            "mode": "interleaved",
            "partitions": {
                "victim": {"share": layout.victim_share},
                "aggressor": {"share": layout.aggressor_share},
            },
        },
    }
    config = {
        "schema_version": 2,
        "global_options": options,
        "experiments": experiments(mode, victim, ppns, sizes),
    }
    return stem, config


class UsageError(Exception):
    """A bad command line; the message is the one line printed on stderr."""


def parse_ppns(text: str) -> list[str]:
    """`--aggr-ppn`: a comma list of 1, 2, 4, 8 and `same`."""
    words = [w.strip() for w in text.split(",")]
    for word in words:
        if word != "same" and word not in AGGRESSOR_PPN_CHOICES:
            raise UsageError(f"--aggr-ppn: {word!r} is not one of 1, 2, 4, 8 or same")
    return words


def parse_sizes(text: str) -> list[int]:
    """`--aggr-msgsize`: a comma list of positive byte counts."""
    sizes: list[int] = []
    for word in text.split(","):
        try:
            size = int(word)
        except ValueError:
            size = 0
        if size < 1:
            raise UsageError(f"--aggr-msgsize: {word.strip()!r} is not a positive integer")
        sizes.append(size)
    return sizes


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Write the GPU comm-only suites of the NPLB lab round.")
    p.add_argument("--out", type=Path, required=True, help="directory for the config files")
    p.add_argument("--smoke", action="store_true", help="ppn 1 and 8, two sizes, -iter 20")
    p.add_argument("--local", action="store_true", help="laptop files, only to drive the flow")
    p.add_argument(
        "--aggr-ppn",
        default="same",
        help="aggressor ranks per node: 1, 2, 4, 8 or same (the victim's), comma list allowed",
    )
    p.add_argument(
        "--aggr-msgsize",
        default=str(AGGRESSOR_SIZE),
        help="aggressor message size in bytes, comma list allowed (default: 2 MiB)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.smoke and args.local:
            raise UsageError("--smoke and --local cannot be combined")
        ppns, sizes = parse_ppns(args.aggr_ppn), parse_sizes(args.aggr_msgsize)
    except UsageError as exc:
        print(f"gpu_set.py: error: {exc}", file=sys.stderr)
        return 2
    mode = SMOKE if args.smoke else LOCAL if args.local else FULL
    args.out.mkdir(parents=True, exist_ok=True)
    for victim in VICTIMS:
        stem, config = build(mode, victim, ppns, sizes)
        (args.out / f"{stem}.json").write_text(json.dumps(config, indent=2) + "\n")
        print(f"  {stem}.json: {len(config['experiments'])} experiments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
