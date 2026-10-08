"""Per-user state of the local scheduler: job ids, job records, and where they live.

Everything lives under `$XDG_STATE_HOME/crab/jobs/` (default `~/.local/state/crab/jobs/`):
`counter` holds the last allocated number (guarded by an exclusive `flock` on `counter.lock`),
and `local-<n>.json` is the record of job `local-<n>`. Records and the counter are replaced
atomically (write a temp file, then `os.replace`), so a reader never sees a half-written file.

A record is never overwritten: it is published with an exclusive hard link, and `allocate_id` skips
numbers whose record exists (a deleted or reset counter cannot reuse an id).

The record is plain JSON, so it is untrusted input: `read_record` refuses any wrong shape instead
of returning it, and the pid is only accepted as a real `int` (never a bool or a string).

`fcntl` is POSIX only (Linux and macOS), like the rest of the local scheduler.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

_LOCAL_ID = re.compile(r"local-[1-9][0-9]*")
_SLURM_ID = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class JobRecord:
    """One local job: its id, the host that started it, and the process that supervises it."""

    id: str
    host: str
    data_dir: str
    supervisor_pid: int
    created: str


def state_dir(environ: Mapping[str, str]) -> Path:
    """`$XDG_STATE_HOME/crab`, or `~/.local/state/crab` when the variable is unset, empty or
    relative (the XDG spec says a relative path is invalid and must be ignored)."""
    xdg = environ.get("XDG_STATE_HOME", "")
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / "crab"
    return Path.home() / ".local" / "state" / "crab"


def jobs_dir(environ: Mapping[str, str]) -> Path:
    """The folder holding the counter and every job record."""
    return state_dir(environ) / "jobs"


def route(job_id: str) -> Literal["local", "slurm"]:
    """Which scheduler owns this job id: `local-<n>` is local, all digits is Slurm.

    Raises:
        ValueError: any other shape (never guessed).
    """
    if _LOCAL_ID.fullmatch(job_id):
        return "local"
    if _SLURM_ID.fullmatch(job_id):
        return "slurm"
    raise ValueError(
        f"invalid job id {job_id!r}: expected 'local-<n>' (a local job) or digits only (a Slurm job)"
    )


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` so that a reader sees the old file or the whole new one."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def allocate_id(jobs: Path) -> str:
    """The next job id (`local-<n>`), unique across concurrent callers.

    The counter is read, incremented and replaced while an exclusive lock on `counter.lock`
    is held.

    Raises:
        ValueError: the counter file does not hold a non-negative integer.
    A number whose `local-<n>.json` already exists is skipped, so a counter that was deleted or
    reset never hands out an id that a record already uses.
    """
    jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
    counter = jobs / "counter"
    fd = os.open(jobs / "counter.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        last = 0
        if counter.exists():
            text = counter.read_text().strip()
            if not re.fullmatch(r"[0-9]+", text):
                raise ValueError(
                    f"job counter {counter} does not hold a number: {text!r}; "
                    "fix or delete this file"
                )
            last = int(text)
        number = last + 1
        while (jobs / f"local-{number}.json").exists():
            number += 1
        _atomic_write(counter, f"{number}\n")
    finally:
        os.close(fd)  # closing the descriptor releases the lock
    return f"local-{number}"


def write_record(jobs: Path, record: JobRecord) -> None:
    """Write the record of a job, atomically and never over an existing record.

    The complete file is written under a temporary name and then hard-linked to its final name,
    which fails if that name exists: a reader sees no file or the whole file.

    Raises:
        ValueError: a record for this id already exists.
    """
    jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = jobs / f"{record.id}.json"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(asdict(record)))
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise ValueError(f"job record {path} already exists; not overwriting it") from exc
    finally:
        tmp.unlink(missing_ok=True)


def read_record(jobs: Path, job_id: str) -> JobRecord | None:
    """The record of a local job, or None when there is none.

    Raises:
        ValueError: `job_id` is not a local id, or the file is not valid JSON of the expected
            shape (the message names the file).
    """
    if route(job_id) != "local":
        raise ValueError(f"invalid job id {job_id!r}: expected 'local-<n>' for a local job")
    path = jobs / f"{job_id}.json"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read job record {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"job record {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"job record {path} is not a JSON object")
    pid = data.get("supervisor_pid")
    strings_ok = (
        data.get("id") == job_id
        and isinstance(data.get("host"), str)
        and isinstance(data.get("data_dir"), str)
        and os.path.isabs(data["data_dir"])
        and isinstance(data.get("created"), str)
    )
    if type(pid) is not int or not strings_ok:
        raise ValueError(
            f"job record {path} has the wrong shape: expected id {job_id!r}, string host and "
            "created, an absolute data_dir and an integer supervisor_pid"
        )
    return JobRecord(job_id, data["host"], data["data_dir"], pid, data["created"])


def check_host(record: JobRecord, here: str) -> None:
    """Refuse a record written on another host: its pid means nothing here.

    Raises:
        ValueError: naming the record's host and this one.
    """
    if record.host != here:
        raise ValueError(
            f"job {record.id} was started on host {record.host!r}, this host is {here!r}"
        )
