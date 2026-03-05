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

All stdout is streamed and appended to an output text file.

Usage:
    python slurm_scheduler.py <N> --nodelist [node01,node02,...] [--output FILE] [--srun-extra "..."]

Examples:
    python slurm_scheduler.py 10 --nodelist [node01,node02,node03,node04,node05,node06,node07,node08]
    python slurm_scheduler.py 20 --nodelist [node01,node02,node03,node04] --output results.txt
"""

import argparse
import math
import random
import subprocess
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# CONFIGURATION — edit application paths to match your environment
# ---------------------------------------------------------------------------

MICROJOB_APPS: list[str] = [
    "/path/to/micro_app_1",
    "/path/to/micro_app_2",
    "/path/to/micro_app_3",
    "/path/to/micro_app_4",
]

MEDIUM_JOB_APPS: list[str] = [
    "/path/to/medium_app_1",
    "/path/to/medium_app_2",
    "/path/to/medium_app_3",
]

# Node layout
MICROJOB_NODE_COUNT = 2       # microjobs always use exactly 2 nodes
MEDIUM_NODE_CHOICES = [4, 8]  # medium jobs randomly use 4 or 8 nodes
TASKS_PER_NODE      = 4
CPUS_PER_TASK       = 1

DEFAULT_OUTPUT_FILE = "scheduler_output.txt"
POLL_INTERVAL       = 2       # seconds between polls

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)

def compute_split(n: int) -> tuple[int, int]:
    """Return (n_micro, n_medium) summing to n with an ~80/20 split."""
    n_medium = max(1, math.floor(n * 0.20))
    n_micro  = n - n_medium
    return n_micro, n_medium

def parse_nodelist(raw: str) -> list[str]:
    """Parse [node01,node02,node03] into ['node01', 'node02', 'node03']."""
    raw = raw.strip().lstrip("[").rstrip("]")
    return [n.strip() for n in raw.split(",") if n.strip()]

def pick_nodes(all_nodes: list[str], job_type: str) -> list[str]:
    """Sample nodes without replacement for one job."""
    if job_type == "micro":
        count = MICROJOB_NODE_COUNT
    else:
        available = [c for c in MEDIUM_NODE_CHOICES if c <= len(all_nodes)]
        count = random.choice(available if available else [min(MEDIUM_NODE_CHOICES)])
        count = min(count, len(all_nodes))
    return random.sample(all_nodes, count)

def pick_app(job_type: str) -> str:
    pool = MICROJOB_APPS if job_type == "micro" else MEDIUM_JOB_APPS
    if not pool:
        raise ValueError(f"No application paths defined for job type '{job_type}'.")
    return random.choice(pool)

# ---------------------------------------------------------------------------
# Job launch
# ---------------------------------------------------------------------------

def launch(job_type: str, nodes: list[str], extra_flags: list[str],
           output_file: str, task_id: int) -> subprocess.Popen:
    """Launch one srun job and return its Popen handle."""
    uid      = f"{job_type}_{task_id}"
    app      = pick_app(job_type)
    nodelist = ",".join(nodes)

    cmd = [
        "srun",
        f"--nodes={len(nodes)}",
        f"--nodelist={nodelist}",
        f"--ntasks-per-node={TASKS_PER_NODE}",
        f"--cpus-per-task={CPUS_PER_TASK}",
        f"--job-name={uid}",
        *extra_flags,
        app,
    ]

    sep = "=" * 72
    header = (
        f"\n{sep}\n"
        f"TASK     : {uid}\n"
        f"TYPE     : {job_type}\n"
        f"NODES    : {nodelist}\n"
        f"APP      : {app}\n"
        f"CMD      : {' '.join(cmd)}\n"
        f"STARTED  : {ts()}\n"
        f"{sep}\n"
    )
    log(f"START [{uid}]  nodes={nodelist}  app={app}")
    with open(output_file, "a") as f:
        f.write(header)

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
    proc.app      = app       # type: ignore[attr-defined]
    return proc

# ---------------------------------------------------------------------------
# Output draining
# ---------------------------------------------------------------------------

def drain_output(proc: subprocess.Popen, output_file: str) -> None:
    """Read all remaining stdout from a finished process and write to file."""
    if proc.stdout:
        for line in proc.stdout:
            with open(output_file, "a") as f:
                f.write(line)

# ---------------------------------------------------------------------------
# Main scheduler loop
# ---------------------------------------------------------------------------

def run_scheduler(n: int, all_nodes: list[str], output_file: str,
                  extra_flags: list[str]) -> None:

    n_micro, n_medium = compute_split(n)

    # Validate node pool
    if len(all_nodes) < MICROJOB_NODE_COUNT:
        log(f"ERROR: Need at least {MICROJOB_NODE_COUNT} nodes for microjobs, "
            f"got {len(all_nodes)}.")
        sys.exit(1)
    if len(all_nodes) < min(MEDIUM_NODE_CHOICES):
        log(f"ERROR: Need at least {min(MEDIUM_NODE_CHOICES)} nodes for medium jobs, "
            f"got {len(all_nodes)}.")
        sys.exit(1)

    # Write header
    with open(output_file, "w") as f:
        f.write(
            f"SLURM srun Scheduler Output\n"
            f"Started  : {ts()}\n"
            f"Total N  : {n}  (micro={n_micro}, medium={n_medium})\n"
            f"Nodes    : {', '.join(all_nodes)}\n"
            f"Layout   : micro={MICROJOB_NODE_COUNT} nodes x {TASKS_PER_NODE} tasks/node x {CPUS_PER_TASK} cpu/task\n"
            f"           medium={MEDIUM_NODE_CHOICES} nodes x {TASKS_PER_NODE} tasks/node x {CPUS_PER_TASK} cpu/task\n"
            f"{'=' * 72}\n"
        )

    task_id = 0

    # Launch initial batch
    running: list[subprocess.Popen] = []
    for job_type, count in [("micro", n_micro), ("medium", n_medium)]:
        for _ in range(count):
            nodes = pick_nodes(all_nodes, job_type)
            proc  = launch(job_type, nodes, extra_flags, output_file, task_id)
            running.append(proc)
            task_id += 1

    log(f"All {n} jobs submitted. Monitoring for completions…")

    # Poll loop
    while running:
        time.sleep(POLL_INTERVAL)
        still_running = []
        for proc in running:
            ret = proc.poll()
            if ret is None:
                # Still running — drain any buffered output lines
                if proc.stdout:
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        with open(output_file, "a") as f:
                            f.write(line)
                still_running.append(proc)
            else:
                # Finished — drain remaining output and write footer
                drain_output(proc, output_file)
                sep = "=" * 72
                footer = (
                    f"{sep}\n"
                    f"FINISHED : {ts()}  exit_code={ret}\n"
                    f"{sep}\n"
                )
                with open(output_file, "a") as f:
                    f.write(footer)
                log(f"FINISH [{proc.uid}]  exit_code={ret}  nodes={','.join(proc.nodes)}")

                # Launch replacement of the same type reusing the same nodelist
                replacement = launch(
                    proc.job_type, proc.nodes, extra_flags, output_file, task_id
                )
                still_running.append(replacement)
                task_id += 1

        running = still_running
        log(f"Active jobs: {len(running)}/{n}")




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
    parser.add_argument("N", type=int,
                        help="Total number of concurrent srun jobs to maintain.")
    parser.add_argument("--nodelist", required=True, metavar="NODELIST",
                        help="Nodes in bracket format: [node01,node02,...].")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE, metavar="FILE",
                        help=f"Aggregated output file (default: {DEFAULT_OUTPUT_FILE}).")
    parser.add_argument("--srun-extra", default="", metavar="FLAGS",
                        help='Extra srun flags for every launch, e.g. "--mem=4G".')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.N < 1:
        print("ERROR: N must be at least 1.", file=sys.stderr)
        sys.exit(1)

    nodes = parse_nodelist(args.nodelist)
    if not nodes:
        print(f"ERROR: No nodes parsed from '{args.nodelist}'", file=sys.stderr)
        sys.exit(1)

    extra_flags = args.srun_extra.split() if args.srun_extra.strip() else []
    run_scheduler(args.N, nodes, args.output, extra_flags)


if __name__ == "__main__":
    main()