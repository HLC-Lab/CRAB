#!/usr/bin/env python3
"""A fake Blink for the laptop rehearsal (`rehearse.sh`): the three NPLB programs, no GPU, no MPI calls.

    fake_blink.py PROGRAM [-msgsize S] [-iter N] [-warmup W] [-maxsamples M] [-grty G] [-gpu]
                          [-endl] [-blength T] [-bpause T]

PROGRAM is `alltoall_comm_only`, `allgather_comm_only` or `allreduce_b`. A measured run (`-iter N`)
prints what Blink's `write_results` prints and the crab-wrappers parser reads: a preamble line (and
the GPU mode line with `-gpu`), the header `Average,Minimum,Maximum,Median,MainRank`, one row of five
seconds values per kept sample (`min(N, M)` rows; `-maxsamples` defaults to 1000 as in Blink) and
`Ran <N+W> iterations. Measured <rows> iterations.`. An aggressor (`-endl`) prints nothing and runs
until it gets SIGTERM, which is how CRAB stops an app with `end: f` (`stop_processes` signals the
process group; mpirun forwards it to the ranks); it then exits 0. `-blength` and `-bpause` are
accepted and ignored. A call with neither `-iter` nor `-endl`, an unknown program or an unknown flag
exits 2 with a usage message.

The numbers are a model, not a measurement: a sample takes `base + size * k` seconds (a different pair
per program) times a deterministic jitter of a few percent (a hash of program, size, condition and
sample number, so a rerun prints the same table), and times 1.6 when the environment has
`NPLB_FAKE_CONTENTION=1`, so a report can show a slowdown above 1 for a contended condition. Only rank
0 (`OMPI_COMM_WORLD_RANK`, default 0) prints; every rank sleeps `NPLB_FAKE_SLEEP` seconds (default
0.2) so that a job takes some time. The `-endl` ranks give up after `NPLB_FAKE_ENDL_MAX` seconds
(default 600) in case nobody stops them.

Stdlib only, Python 3.10. Exit 0, or 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import sys
import time
from collections.abc import Sequence

HEADER = "Average,Minimum,Maximum,Median,MainRank"
GPU_LINE = "GPU mode: backend CUDA, 8 device(s) visible; MPI reports GPU support: yes"
DEFAULT_MAXSAMPLES = 1000  # Blink keeps this many samples when -maxsamples is not given
DEFAULT_MSGSIZE = 1024  # Blink's own default
CONTENTION_FACTOR = 1.6
# program -> (seconds at size 0, seconds per byte)
MODEL = {
    "alltoall_comm_only": (20e-6, 2.0e-10),
    "allgather_comm_only": (15e-6, 1.5e-10),
    "allreduce_b": (25e-6, 2.5e-10),
}
BANNER = {
    "alltoall_comm_only": "All-to-all",
    "allgather_comm_only": "All-gather",
    "allreduce_b": "All-reduce",
}


def unit(*parts: object) -> float:
    """A deterministic number in [0, 1) from the parts."""
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def sample_row(program: str, size: int, contended: bool, index: int) -> str:
    """One CSV row (Average, Minimum, Maximum, Median, MainRank, in seconds) of sample `index`."""
    base, per_byte = MODEL[program]
    average = (base + size * per_byte) * (0.95 + 0.10 * unit(program, size, contended, index, "a"))
    if contended:
        average *= CONTENTION_FACTOR
    values = [
        average,
        average * (0.90 + 0.05 * unit(program, size, contended, index, "min")),
        average * (1.05 + 0.10 * unit(program, size, contended, index, "max")),
        average * (0.98 + 0.04 * unit(program, size, contended, index, "med")),
        average * (0.95 + 0.10 * unit(program, size, contended, index, "main")),
    ]
    return ",".join(f"{value:.9f}" for value in values)


def results_text(
    program: str, size: int, iters: int, warmup: int, maxsamples: int, gpu: bool, contended: bool
) -> str:
    """What rank 0 prints for a measured run."""
    kept = min(iters, maxsamples)
    lines = [f"{BANNER[program]} with 4 processes, msg-size: {size}, test iterations: {iters}."]
    if gpu:
        lines.append(GPU_LINE)
    lines.append(HEADER)
    lines.extend(sample_row(program, size, contended, i) for i in range(kept))
    lines.append(f"Ran {iters + warmup} iterations. Measured {kept} iterations.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fake_blink.py",
        description="Fake Blink for the laptop rehearsal: measured run (-iter) or aggressor (-endl).",
        allow_abbrev=False,
    )
    parser.add_argument("program", choices=sorted(MODEL))
    parser.add_argument("-msgsize", type=int, default=DEFAULT_MSGSIZE, help="message size in bytes")
    parser.add_argument("-iter", dest="iters", type=int, help="measured iterations")
    parser.add_argument("-warmup", type=int, default=0, help="warm-up iterations")
    parser.add_argument("-maxsamples", type=int, default=DEFAULT_MAXSAMPLES)
    parser.add_argument("-grty", type=int, default=1, help="accepted, ignored")
    parser.add_argument("-gpu", action="store_true", help="buffers in GPU memory (a preamble line)")
    parser.add_argument("-endl", action="store_true", help="aggressor: run until SIGTERM")
    parser.add_argument("-blength", help="burst length, accepted and ignored (for example 10ms)")
    parser.add_argument("-bpause", help="burst pause, accepted and ignored (for example 10ms)")
    return parser


def run_endless() -> int:
    """The aggressor: wait for SIGTERM (CRAB's stop of an `end: f` app), then exit 0."""
    stopped: list[bool] = []
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stopped.append(True))
    limit = time.monotonic() + float(os.environ.get("NPLB_FAKE_ENDL_MAX", "600"))
    while not stopped and time.monotonic() < limit:
        time.sleep(0.05)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.iters is None and not args.endl:
        parser.error("give -iter N (a measured run) or -endl (an endless aggressor)")
    if args.endl:
        return run_endless()
    if args.iters < 1 or args.maxsamples < 1 or args.warmup < 0 or args.msgsize < 0:
        parser.error("-iter and -maxsamples must be at least 1, -warmup and -msgsize not negative")
    time.sleep(float(os.environ.get("NPLB_FAKE_SLEEP", "0.2")))
    if int(os.environ.get("OMPI_COMM_WORLD_RANK", "0")) == 0:
        contended = os.environ.get("NPLB_FAKE_CONTENTION") == "1"
        text = results_text(
            args.program, args.msgsize, args.iters, args.warmup, args.maxsamples, args.gpu, contended
        )
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
