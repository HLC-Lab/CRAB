"""Appending an experiment's row to the system-level metadata.csv."""

import csv
import fcntl
import os
import re
from collections.abc import Callable
from typing import Any


def write_registry_row(
    exp_dir: str,
    global_opts: dict[str, Any],
    ppn: Any,
    apps: list[Any],
    status: str,
    total_runs: int,
    failed_runs: int,
    log_error: Callable[[str], None],
) -> None:
    """
    Appends a data row for this experiment to the system-level metadata.csv.
    Uses exclusive POSIX file locking to guarantee process safety on shared HPC filesystems.

    `total_runs`/`failed_runs` let a caller distinguish "this experiment's
    overall status latched to FAILED because of one bad run, but N of M
    runs actually succeeded and have data" from "every run failed" (plan
    081). An existing metadata.csv from before this field existed keeps
    its old header forever (no migration, by design) -- new rows still
    append fine, they just aren't readable by these column names.
    """
    try:
        # Traversal: self.exp_dir is system/job_name_timestamp/experiment_name
        job_dir = os.path.dirname(exp_dir)
        system_dir = os.path.dirname(job_dir)
        registry_path = os.path.join(system_dir, "metadata.csv")

        job_basename = os.path.basename(job_dir)
        exp_basename = os.path.basename(exp_dir)

        # Extract standard ISO-like timestamp from the job folder suffix
        timestamp = "unknown"
        ts_match = re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", job_basename)
        if ts_match:
            timestamp = ts_match.group(0)

        # Safely gather metadata parameters
        job_name = global_opts.get("name", "unknown")
        numnodes = global_opts.get("numnodes", 1)

        # Space-separated, alphabetically sorted unique application identifiers
        unique_apps = sorted(
            list(
                set(
                    [
                        str(getattr(app, "benchmark_id", getattr(app, "name", "unknown")))
                        for app in apps
                    ]
                )
            )
        )
        apps_list = " ".join(unique_apps)

        tags = global_opts.get("tags", "none")
        relative_path = f"./{job_basename}/{exp_basename}"

        headers = [
            "job_name",
            "experiment_name",
            "timestamp",
            "numnodes",
            "ppn",
            "apps_list",
            "status",
            "tags",
            "relative_path",
            "total_runs",
            "failed_runs",
        ]
        row = [
            job_name,
            exp_basename,
            timestamp,
            numnodes,
            ppn,
            apps_list,
            status,
            tags,
            relative_path,
            total_runs,
            failed_runs,
        ]

        # Atomic append routine using advisory locking
        with open(registry_path, "a+", newline="") as f:
            # Acquire exclusive lock. Blocks execution until other CRAB instances release it.
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

            # Move pointer to check if file is completely new/empty
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                writer = csv.writer(f)
                writer.writerow(headers)

            writer = csv.writer(f)
            writer.writerow(row)

            # Force filesystem sync before clearing the block lock
            f.flush()
            os.fsync(f.fileno())

            # Release lock explicitly
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    except Exception as e:
        # Fallback guardrail to prevent a registry I/O bottleneck from crashing a study
        log_error(f"CRAB Registry execution hook failed: {e}")
