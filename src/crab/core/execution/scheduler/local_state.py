"""Per-user state of the local scheduler: job ids, job records, and where they live.

Everything lives under `$XDG_STATE_HOME/crab/` (default `~/.local/state/crab/`):
- `jobs/counter` holds the last allocated number (guarded by an exclusive `flock` on
  `jobs/counter.lock`), and `jobs/local-<n>.json` is the record of job `local-<n>`;
- `jobs/local-<n>.lock` is held (`LOCK_EX`) by the job's supervisor for as long as it lives:
  a held lock means alive, and the kernel drops it when the process dies, however it dies;
- `hosts/<name>.lock` is held by a job that needs the host exclusively.
Records and the counter are replaced atomically (write a temp file, then `os.replace`), so a
reader never sees a half-written file. Each job also has `local_state.json` in its own data
directory, written the same way (`write_job_state` / `read_job_state`).

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
from datetime import datetime
from pathlib import Path
from typing import Literal

_LOCAL_ID = re.compile(r"local-[1-9][0-9]*")
_SLURM_ID = re.compile(r"[0-9]+")

# The states a supervisor writes. LOST is never written: it is derived when the supervisor died
# without a final state.
JOB_STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT")
_STATE_FILE = "local_state.json"


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


def open_liveness_lock(jobs: Path, job_id: str) -> int:
    """Create `jobs/<id>.lock` and return a descriptor that holds `LOCK_EX` on it.

    The supervisor calls this once and keeps the descriptor open until it exits. Python opens it
    non-inheritable, so the job's own processes do not keep the lock alive after the supervisor
    is gone.

    Raises:
        ValueError: `job_id` is not a local id.
        BlockingIOError: another descriptor already holds the lock (never waits).
    """
    if route(job_id) != "local":
        raise ValueError(f"invalid job id {job_id!r}: expected 'local-<n>' for a local job")
    jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(jobs / f"{job_id}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


def is_alive(jobs: Path, job_id: str) -> bool:
    """Whether some process holds the job's liveness lock.

    The probe takes a shared lock without waiting and drops it at once; it never creates the
    lock file. A supervisor that tries to take its lock in the same instant gets
    `BlockingIOError` and has to retry.

    Raises:
        ValueError: `job_id` is not a local id.
    """
    if route(job_id) != "local":
        raise ValueError(f"invalid job id {job_id!r}: expected 'local-<n>' for a local job")
    try:
        fd = os.open(jobs / f"{job_id}.lock", os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)  # closing the descriptor releases the probe's lock


def _check_host_name(name: str) -> None:
    if not name or "/" in name or "\0" in name or name.startswith("."):
        raise ValueError(
            f"invalid host name {name!r} for a lock file: it must be non-empty, "
            "contain no '/' and not start with '.'"
        )


def acquire_hosts(state_root: Path, names: list[str], blocking: bool = True) -> list[int]:
    """Lock `state_root/hosts/<name>.lock` exclusively for each distinct name, in sorted order.

    A job that needs a host exclusively holds its lock while it runs. Taking the locks in one
    global order means two jobs that need overlapping hosts cannot wait on each other. With
    `blocking=False` a lock held elsewhere raises at once, after the locks already taken are
    released.

    Returns:
        The descriptors, to pass to `release_hosts`.

    Raises:
        ValueError: a name is empty, contains `/` or starts with `.` (nothing is locked).
        BlockingIOError: `blocking` is False and a host is held by someone else.
    """
    distinct = sorted(set(names))
    for name in distinct:
        _check_host_name(name)
    hosts = state_root / "hosts"
    hosts.mkdir(parents=True, exist_ok=True, mode=0o700)
    held: list[int] = []
    try:
        for name in distinct:
            fd = os.open(hosts / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            held.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        release_hosts(held)
        raise
    return held


def release_hosts(fds: list[int]) -> None:
    """Close the descriptors from `acquire_hosts`, which releases their locks."""
    for fd in fds:
        os.close(fd)


def write_job_state(data_dir: Path, state: str) -> None:
    """Replace `data_dir/local_state.json` with `state` and the current time, atomically.

    Raises:
        ValueError: `state` is not one of `JOB_STATES` (LOST is derived, never written).
    """
    if state not in JOB_STATES:
        raise ValueError(f"invalid job state {state!r}: expected one of {', '.join(JOB_STATES)}")
    body = {"state": state, "at": datetime.now().isoformat(timespec="seconds")}
    _atomic_write(data_dir / _STATE_FILE, json.dumps(body))


def read_job_state(data_dir: Path) -> str | None:
    """The state in `data_dir/local_state.json`, or None when there is no such file.

    Raises:
        ValueError: the file is unreadable, not valid JSON, or does not hold a state from
            `JOB_STATES` (the message names the file).
    """
    path = data_dir / _STATE_FILE
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read job state {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"job state {path} is not valid JSON: {exc}") from exc
    state = data.get("state") if isinstance(data, dict) else None
    if not isinstance(state, str) or state not in JOB_STATES:
        raise ValueError(f"job state {path} does not hold a state from {', '.join(JOB_STATES)}")
    return state
