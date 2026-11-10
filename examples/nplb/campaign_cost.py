"""Time cost of one victim iteration, to choose `-iter` of the contention campaign (stdlib only).

`campaign.py` wants every victim point to take about `TARGET_S` seconds, so `-iter` is
`TARGET_S / (seconds per iteration)`. This module holds the seconds per iteration it divides by.
Everything it cannot read from a measurement is an assumption, labelled as such in its source tag
(`iter_time` returns `(seconds, source)`; tags of one value are joined by `+`), so the estimate
report can flag it:

* `measured`: read from the October table below (a2a and agtr, split layouts, no contention).
* `extrapolated`: a2a at 256 MiB, which the October run could not measure (see the table).
* `proxy-ppn8`: the `shared` layout (4 ranks per node of each app) has no measurement; the
  16-rank numbers of the table stand in for it. Unmeasured.
* `model-ardc`: allreduce has no measurement; its time is `ALLREDUCE_FACTOR` x the all-to-all
  time of the same size. Unmeasured planning assumption.
* `assumed-contention`: a victim that runs against an aggressor takes `CONTENTION_FACTOR` x its
  alone time. Unmeasured planning assumption.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

# Seconds per iteration of the victim alone, on 2 H100 nodes with 8 ranks each (16 ranks, the rank
# count of the campaign's victim). Source: the owner's October 2026 runs `a2a_run1.tgz` and
# `ag_run1.tgz` (config `ppn8_alone`, buffers in GPU memory, `-gpu`); per size, the median over the
# samples of the `Max-Duration_s` column (the slowest rank: a collective ends when its last rank
# does). Taken on 2026-11-10. The a2a app of 256 MiB failed in that run (`ibv_reg_mr` error while
# registering memory), so it has no number here; `A2A_256M_FACTOR` extrapolates it. The allgather
# values do not grow smoothly (1 MiB took 13.6 ms but 4 MiB only 1.6 ms): they are kept as
# measured, the likely cause is a protocol switch in the October build, not noise.
MIB = 1 << 20
MEASURED_S: dict[str, dict[int, float]] = {
    "a2a": {
        16 * 1024: 0.000117461,
        64 * 1024: 0.0001247115,
        256 * 1024: 0.0001527715,
        1 * MIB: 0.000306709,
        4 * MIB: 0.00099164,
        16 * MIB: 0.0043763885,
        64 * MIB: 0.0156847505,
    },
    "agtr": {
        16 * 1024: 0.000509986,
        64 * 1024: 0.0013434825,
        256 * 1024: 0.0052507995,
        1 * MIB: 0.01358388,
        4 * MIB: 0.0015963795,
        16 * MIB: 0.0056185845,
        64 * MIB: 0.02165393,
        256 * MIB: 0.085716337,
    },
}
# The a2a time of 256 MiB is 4 x that of 64 MiB: at these sizes the time is the data volume over
# the bandwidth (16 -> 64 MiB measured x3.6), and 256 MiB carries 4 x the data. Extrapolated.
A2A_256M_FACTOR = 4.0
CONTENTION_FACTOR = 2.0  # UNMEASURED planning assumption: contended = 2 x alone
ALLREDUCE_FACTOR = 2.0  # UNMEASURED planning assumption: allreduce = 2 x all-to-all, same size
TARGET_S = 20.0  # wanted time of one victim point
ITER_MIN = 100
ITER_MAX = 20000
LAYOUTS = ("split-i", "split-l", "shared")
START_UP_S = 10.0  # about 10 s of mpirun start-up per app
MARGIN = 1.5  # walltime slack over the estimate
MIN_WALLTIME_S = 30 * 60
MIN_TIMEOUT_S = 600
TIMEOUT_FACTOR = 5


def _base(victim: str, size: int) -> tuple[float, str]:
    """(seconds, tag) of one size on the split layouts without contention: measured or modelled."""
    if victim == "ardc":
        return ALLREDUCE_FACTOR * _base("a2a", size)[0], "model-ardc"
    table = MEASURED_S[victim]
    if size in table:
        return table[size], "measured"
    if victim == "a2a" and size == 256 * MIB:
        return A2A_256M_FACTOR * table[64 * MIB], "extrapolated"
    raise ValueError(f"no cost for message size {size}: the table has {sorted(MEASURED_S['agtr'])}")


def iter_time(victim: str, size: int, layout: str, contended: bool) -> tuple[float, str]:
    """Seconds per victim iteration, and where the number comes from.

    Args:
        victim: `a2a`, `agtr` or `ardc`.
        size: message size in bytes, one of the table's eight.
        layout: `split-i`, `split-l` or `shared`.
        contended: whether an aggressor runs at the same time.

    Returns:
        (seconds, source): the tags joined by `+`, in the order measured/extrapolated/model-ardc,
        proxy-ppn8, assumed-contention.

    Raises:
        ValueError: unknown victim, layout or size.
    """
    if victim not in ("a2a", "agtr", "ardc"):
        raise ValueError(f"unknown victim {victim!r}")
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    seconds, tag = _base(victim, size)
    tags = [tag]  # `measured` is dropped as soon as an assumption is added on top of it
    if layout == "shared":
        tags = [t for t in tags if t != "measured"] + ["proxy-ppn8"]
    if contended:
        seconds *= CONTENTION_FACTOR
        tags = [t for t in tags if t != "measured"] + ["assumed-contention"]
    return seconds, "+".join(tags)


def iterations(
    victim: str, size: int, layout: str, contended: bool, target_s: float = TARGET_S
) -> tuple[int, int, str]:
    """(`-iter`, `-warmup`, source) for a point of about `target_s` seconds.

    `-iter` is `target_s / t` rounded and clamped to 100..20000; `-warmup` is a tenth of it, at
    least 10. The source is the one of `iter_time`.
    """
    seconds, source = iter_time(victim, size, layout, contended)
    count = min(ITER_MAX, max(ITER_MIN, round(target_s / seconds)))
    return count, max(10, count // 10), source


class Estimate(NamedTuple):
    """Expected run of one block: all apps, seconds (no walltime margin), victim time per tag."""

    seconds: float
    apps: int
    victim_s: dict[str, float]


def block_estimate(
    victim: str, layout: str, sizes: Sequence[int], contended: Sequence[bool], local: bool = False
) -> Estimate:
    """What `campaign.victim_chain` adds up, for one experiment per entry of `contended`.

    Every app costs `START_UP_S`; the victim of each size adds `(iter + warmup) x seconds per
    iteration`, filed under its source tag in `victim_s` (a `local` block has no victim time, the
    laptop run is a few seconds), and an aggressor adds one more start-up.
    """
    seconds = 0.0
    apps = 0
    victim_s: dict[str, float] = {}
    for with_aggressor in contended:
        for size in sizes:
            seconds += START_UP_S
            apps += 1
            if local:
                continue
            count, warmup, source = iterations(victim, size, layout, with_aggressor)
            point = (count + warmup) * iter_time(victim, size, layout, with_aggressor)[0]
            seconds += point
            victim_s[source] = victim_s.get(source, 0.0) + point
        if with_aggressor:
            seconds += START_UP_S
            apps += 1
    return Estimate(seconds, apps, victim_s)


def walltime_needed(estimates: Sequence[float]) -> int:
    """Walltime in seconds of a block from the estimated seconds of its experiments."""
    return math.ceil(MARGIN * sum(estimates))


def timeout_s(estimates: Sequence[float]) -> int:
    """`timeout` of a block (CRAB applies it to each experiment): the largest estimate x5."""
    return max(MIN_TIMEOUT_S, math.ceil(TIMEOUT_FACTOR * max(estimates)))


def hhmmss(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
