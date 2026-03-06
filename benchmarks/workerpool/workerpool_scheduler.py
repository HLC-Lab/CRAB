#!/usr/bin/env python3
"""
SLURM srun Job Scheduler
=========================
Must be launched INSIDE an existing salloc session.

Maintains N concurrent srun jobs split as:
  - 80% microjobs  : 2 nodes, 4 tasks/node, 1 cpu/task
  - 20% medium jobs: 4 or 8 nodes (chosen randomly), 4 tasks/node, 1 cpu/task

The main loop polls all running processes. When one finishes, its nodelist is
reused to immediately launch a replacement of the same type.

Each job writes its stdout to its own file inside workerpool_out/.
Scheduler debug logs go to workerpool_out/scheduler.log.

Usage:
    python slurm_scheduler.py <N> --nodelist [node01,node02,...] [--output-dir DIR] [--srun-extra "..."]

Examples:
    python slurm_scheduler.py 10 --nodelist [node01,node02,node03,node04,node05,node06,node07,node08]
    python slurm_scheduler.py 20 --nodelist [node01,node02,node03,node04] --output-dir my_out
"""

import argparse
import math
import os
import random
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION — edit application paths to match your environment
# ---------------------------------------------------------------------------

MICROJOB_APPS: list[str] = [
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
]

MEDIUM_JOB_APPS: list[str] = [
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
    "$HOME/CRAB/benchmarks/blink/bin/a2a_b -msgsize 512 -iter 1000",
]

# Node layout
MICROJOB_NODE_COUNT = 2       # microjobs always use exactly 2 nodes
MEDIUM_NODE_CHOICES = [4, 8]  # medium jobs randomly use 4 or 8 nodes
TASKS_PER_NODE      = 4
CPUS_PER_TASK       = 1

DEFAULT_OUTPUT_DIR  = "workerpool_out"
SCHEDULER_LOG_NAME  = "scheduler.log"
POLL_INTERVAL       = 2       # seconds between polls

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str, log_path: Path) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")

def compute_split(n: int) -> tuple[int, int]:
    """Return (n_micro, n_medium) summing to n with an ~80/20 split."""
    n_medium = max(1, math.floor(n * 0.20))
    n_micro  = n - n_medium
    return n_micro, n_medium

def parse_nodelist(raw: str) -> tuple[str, list[str]]:
    """Parse name[node01,node02,node03] into (name, ['node01', 'node02', 'node03'])."""
    nodelist_raw = raw.strip().split("[")
    name  = nodelist_raw[0]
    nodes = nodelist_raw[1].rstrip("]")
    return (name, [n.strip() for n in nodes.split(",") if n.strip()])

def pick_nodes(all_nodes: list[str], job_type: str) -> list[str]:
    """Sample nodes without replacement for one job."""
    if job_type == "micro":
        count = MICROJOB_NODE_COUNT
    else:
        available = [c for c in MEDIUM_NODE_CHOICES if c <= len(all_nodes)]
        count = random.choice(available if available else [min(MEDIUM_NODE_CHOICES)])
        count = min(count, len(all_nodes))
    return random.sample(all_nodes, count)

def expand_app(app_str: str) -> list[str]:
    """
    Expand environment variables (e.g. $HOME) and split the app string into
    a proper argv list so srun receives the executable and its flags separately.
    """
    expanded = os.path.expandvars(os.path.expanduser(app_str))
    return shlex.split(expanded)

def pick_app(job_type: str) -> str:
    pool = MICROJOB_APPS if job_type == "micro" else MEDIUM_JOB_APPS
    if not pool:
        raise ValueError(f"No application paths defined for job type '{job_type}'.")
    return random.choice(pool)

def job_output_path(out_dir: Path, uid: str) -> Path:
    """Return the per-job output file path."""
    return out_dir / f"{uid}.out"

# ---------------------------------------------------------------------------
# Job launch
# ---------------------------------------------------------------------------

def launch(job_type: str, name: str, nodes: list[str], extra_flags: list[str],
           out_dir: Path, log_path: Path, task_id: int) -> subprocess.Popen:
    """Launch one srun job and return its Popen handle."""
    uid      = f"{job_type}_{task_id}"
    app      = pick_app(job_type)
    app_argv = expand_app(app)
    nodelist = ",".join(nodes)
    job_out  = job_output_path(out_dir, uid)

    cmd = [
        "srun",
        f"--nodelist={name}[{nodelist}]",
        f"--ntasks-per-node={TASKS_PER_NODE}",
        f"--cpus-per-task={CPUS_PER_TASK}",
        f"--job-name={uid}",
        *extra_flags,
        *app_argv,
    ]

    header = (
        f"TASK     : {uid}\n"
        f"TYPE     : {job_type}\n"
        f"NODES    : {nodelist}\n"
        f"APP      : {app}\n"
        f"CMD      : {' '.join(cmd)}\n"
        f"STARTED  : {ts()}\n"
        f"{'=' * 72}\n"
    )

    # Write header to the per-job output file
    with open(job_out, "w") as f:
        f.write(header)

    log(f"START [{uid}]  nodes={nodelist}  app={app}  out={job_out.name}", log_path)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Attach metadata directly to the Popen object for convenience
    proc.uid      = uid       # type: ignore[attr-defined]
    proc.job_type = job_type  # type: ignore[attr-defined]
    proc.nodes    = nodes     # type: ignore[attr-defined]
    proc.name     = name      # type: ignore[attr-defined]
    proc.app      = app       # type: ignore[attr-defined]
    proc.job_out  = job_out   # type: ignore[attr-defined]
    return proc

# ---------------------------------------------------------------------------
# Output draining
# ---------------------------------------------------------------------------

def drain_output(proc: subprocess.Popen) -> None:
    """Read all remaining stdout from a finished process and append to its job file."""
    if proc.stdout:
        with open(proc.job_out, "a") as f:          # type: ignore[attr-defined]
            for line in proc.stdout:
                f.write(line)

def drain_live(proc: subprocess.Popen) -> None:
    """Non-blocking drain of any currently available lines to the job file."""
    if proc.stdout:
        with open(proc.job_out, "a") as f:          # type: ignore[attr-defined]
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                f.write(line)

# ---------------------------------------------------------------------------
# Main scheduler loop
# ---------------------------------------------------------------------------

def run_scheduler(n: int, name: str, all_nodes: list[str], out_dir: Path,
                  extra_flags: list[str]) -> None:

    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / SCHEDULER_LOG_NAME

    n_micro, n_medium = compute_split(n)

    # Validate node pool
    if len(all_nodes) < MICROJOB_NODE_COUNT:
        log(f"ERROR: Need at least {MICROJOB_NODE_COUNT} nodes for microjobs, "
            f"got {len(all_nodes)}.", log_path)
        sys.exit(1)
    if len(all_nodes) < min(MEDIUM_NODE_CHOICES):
        log(f"ERROR: Need at least {min(MEDIUM_NODE_CHOICES)} nodes for medium jobs, "
            f"got {len(all_nodes)}.", log_path)
        sys.exit(1)

    # Write scheduler log header
    with open(log_path, "w") as f:
        f.write(
            f"SLURM srun Scheduler Log\n"
            f"Started  : {ts()}\n"
            f"Total N  : {n}  (micro={n_micro}, medium={n_medium})\n"
            f"Nodes    : {', '.join(all_nodes)}\n"
            f"Layout   : micro={MICROJOB_NODE_COUNT} nodes x {TASKS_PER_NODE} tasks/node x {CPUS_PER_TASK} cpu/task\n"
            f"           medium={MEDIUM_NODE_CHOICES} nodes x {TASKS_PER_NODE} tasks/node x {CPUS_PER_TASK} cpu/task\n"
            f"Output   : {out_dir.resolve()}/\n"
            f"{'=' * 72}\n"
        )

    task_id = 0

    # Launch initial batch
    running: list[subprocess.Popen] = []
    for job_type, count in [("micro", n_micro), ("medium", n_medium)]:
        for _ in range(count):
            nodes = pick_nodes(all_nodes, job_type)
            proc  = launch(job_type, name, nodes, extra_flags, out_dir, log_path, task_id)
            running.append(proc)
            task_id += 1

    log(f"All {n} jobs submitted. Monitoring for completions…", log_path)

    # Poll loop
    while running:
        time.sleep(POLL_INTERVAL)
        still_running = []
        for proc in running:
            ret = proc.poll()
            if ret is None:
                drain_live(proc)
                still_running.append(proc)
            else:
                # Finished — drain remaining output and write footer to job file
                drain_output(proc)
                footer = (
                    f"{'=' * 72}\n"
                    f"FINISHED : {ts()}  exit_code={ret}\n"
                )
                with open(proc.job_out, "a") as f:  # type: ignore[attr-defined]
                    f.write(footer)

                log(
                    f"FINISH [{proc.uid}]  exit_code={ret}"                 # type: ignore[attr-defined]
                    f"  nodes={','.join(proc.nodes)}"                        # type: ignore[attr-defined]
                    f"  out={proc.job_out.name}",                            # type: ignore[attr-defined]
                    log_path,
                )

                # Launch replacement of the same type reusing the same nodelist
                replacement = launch(
                    proc.job_type, proc.name, proc.nodes,                   # type: ignore[attr-defined]
                    extra_flags, out_dir, log_path, task_id,
                )
                still_running.append(replacement)
                task_id += 1

        running = still_running
        log(f"Active jobs: {len(running)}/{n}", log_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SLURM srun scheduler — maintains N concurrent jobs (80%% micro / 20%% medium).\n"
            f"  micro  : {MICROJOB_NODE_COUNT} nodes, {TASKS_PER_NODE} tasks/node, {CPUS_PER_TASK} cpu/task\n"
            f"  medium : {MEDIUM_NODE_CHOICES} nodes (random), {TASKS_PER_NODE} tasks/node, {CPUS_PER_TASK} cpu/task"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--jobs", type=int,
                        help="Total number of concurrent srun jobs to maintain.")
    parser.add_argument("--nodelist", required=True, metavar="NODELIST",
                        help="Nodes in bracket format: name[node01,node02,...].")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
                        help=f"Directory for all output files (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--srun-extra", default="", metavar="FLAGS",
                        help='Extra srun flags for every launch, e.g. "--mem=4G".')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.N < 1:
        print("ERROR: N must be at least 1.", file=sys.stderr)
        sys.exit(1)

    name, nodes = parse_nodelist(args.nodelist)
    if not nodes:
        print(f"ERROR: No nodes parsed from '{args.nodelist}'", file=sys.stderr)
        sys.exit(1)

    out_dir     = Path(args.output_dir)
    extra_flags = args.srun_extra.split() if args.srun_extra.strip() else []
    run_scheduler(args.N, name, nodes, out_dir, extra_flags)


if __name__ == "__main__":
    main()