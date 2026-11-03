"""The registry of live app processes and the stop that ends them.

`run_job` (manager.py) registers each app's process group; `stop_processes` sends SIGTERM to the
groups, waits a shared grace, then SIGKILLs what is left, and `stop_all_registered` does that for
everything still registered when a worker is terminated.
"""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from crab.log import CrabLogger

# How long stop_processes waits after SIGTERM before it sends SIGKILL (ADR-034, "Cancel order").
STOP_GRACE_SECONDS = 10.0

# How long stop_processes waits, in all, for the processes it SIGKILLed to be reaped before it
# gives up on the rest.
KILL_WAIT_SECONDS = 5.0

# Live app processes by pid, with the label their log lines use. A worker that is terminated
# stops whatever is left here (stop_all_registered) even when the stop that SIGTERM interrupted
# was the only thing that knew about them.
_registry: dict[int, tuple[subprocess.Popen, str]] = {}
_registry_lock = threading.Lock()


def register_process(proc: subprocess.Popen, label: str) -> None:
    """Remember a live app process; drops entries whose process has already been reaped."""
    with _registry_lock:
        for pid in [
            pid
            for pid, (known, _) in _registry.items()
            if getattr(known, "returncode", None) is not None
        ]:
            del _registry[pid]
        _registry[proc.pid] = (proc, label)


def unregister_process(proc: subprocess.Popen) -> None:
    """Forget an app process that has been reaped."""
    with _registry_lock:
        _registry.pop(proc.pid, None)


def _usable_pgid(pgid: object) -> bool:
    """Whether `pgid` is a process group id CRAB may signal or probe.

    A pid of 1 or less, or CRAB's own group, would reach every process of the user or CRAB itself
    (the 2026-10-08 incident), so those are refused whatever a caller passed.
    """
    return type(pgid) is int and pgid > 1 and pgid != os.getpgrp()


def _group_members(pgid: int) -> bool:
    """Whether process group `pgid` still has a live member (a zombie is not live).

    On Linux this reads /proc: field 5 of `/proc/<pid>/stat` is the group, field 3 the state. The
    command name (field 2) may hold spaces or parentheses, so the line is parsed after its last
    `)`. Where /proc is missing (macOS) it probes with `os.killpg(pgid, 0)`, which also counts a
    zombie as alive; that only makes a wait run to its grace. An unusable `pgid` has no members.
    """
    if not _usable_pgid(pgid):
        return False
    proc_root = Path("/proc")
    if not (proc_root / "self" / "stat").is_file():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    for stat in proc_root.glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
        except OSError:
            continue  # the process exited while we scanned
        state, _ppid, group = text[text.rindex(")") + 2 :].split()[:3]
        if int(group) == pgid and state not in ("Z", "X"):
            return True
    return False


def _signal_group(pgid: int, sig: int, logger: CrabLogger, who: str) -> bool:
    """Send `sig` to process group `pgid`, a group CRAB started (its leader's pid).

    Returns whether the signal may have reached the group. A refused group and a PermissionError
    are logged as a warning naming `who`; a group that is already gone (ProcessLookupError) is the
    normal race and stays silent.

    The pid-reuse argument: on Linux a pid number stays allocated while any process uses it as its
    group id, so a group that still has members cannot have its id handed to someone else. Callers
    signal a group only right after `_group_members` saw a live member (SIGTERM: while the leader
    is unreaped; SIGKILL: just before sending). On macOS the `killpg(pgid, 0)` probe means the same.
    """
    if not _usable_pgid(pgid):
        logger.warning(f"{who}: not signalled, unsafe process group")
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass  # already gone
    except PermissionError:
        logger.warning(f"{who}: not signalled, permission denied")
        return False
    return True


def stop_processes(
    procs: list[subprocess.Popen],
    logger: CrabLogger,
    grace: float | None = None,
    labels: dict[int, str] | None = None,
) -> None:
    """Stop app processes: SIGTERM to each one's group, a shared wait, then SIGKILL for the rest.

    SIGTERM first lets a launcher (mpirun, srun) tear its ranks down; SIGKILL first would orphan
    them. The `grace` seconds are shared by all processes, not given to each, and the wait lasts
    until every group is empty, not only until the leader (the bash wrapper, which exits on SIGTERM
    at once) has exited: a launcher or app child that outlives its wrapper still gets SIGKILL. A
    process that had already exited before this call is never signalled.

    Args:
        procs: the app processes (children started with start_new_session=True, so pid == pgid).
        logger: receives one line per process whose group needed SIGKILL, and a warning for each
            process that was not signalled or was still alive after SIGKILL.
        grace: seconds to wait after SIGTERM before SIGKILL; STOP_GRACE_SECONDS when None.
        labels: optional pid -> name (such as "App 2") used in the log line instead of the pid.
    """
    if grace is None:
        grace = STOP_GRACE_SECONDS
    labels = labels or {}

    def who(pid: int) -> str:
        return f"{labels[pid]} (pid {pid})" if pid in labels else f"PID {pid}"

    # A process whose group cannot be signalled is not waited for: nothing would end it.
    running = [
        proc
        for proc in procs
        if proc.poll() is None and _signal_group(proc.pid, signal.SIGTERM, logger, who(proc.pid))
    ]
    pgids = [proc.pid for proc in running]

    deadline = time.monotonic() + grace
    while True:
        for proc in running:
            proc.poll()  # reap an exited leader, so it is no longer a member
        if not any(_group_members(pgid) for pgid in pgids):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)

    kill_deadline = time.monotonic() + KILL_WAIT_SECONDS
    for proc, pgid in zip(running, pgids, strict=True):
        # Re-check right before sending: the group may have emptied since the loop ended.
        if _group_members(pgid):
            name = labels.get(pgid, f"PID {pgid}")
            logger.warning(f"{name}: still running after {grace:g}s, sending SIGKILL")
            _signal_group(pgid, signal.SIGKILL, logger, who(pgid))
        try:
            proc.wait(timeout=max(0.0, kill_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            logger.warning(f"{who(pgid)} still alive after SIGKILL")
            continue
        unregister_process(proc)

    # Processes that had exited before this call are reaped too (poll() reaps them).
    for proc in procs:
        if proc.poll() is not None:
            unregister_process(proc)


def stop_all_registered(logger: CrabLogger, grace: float | None = None) -> None:
    """Stop every app process still registered, then clear the registry.

    The worker calls this after SIGTERM, so apps are not left running when the stop that the
    signal interrupted never finished.
    """
    with _registry_lock:
        entries = list(_registry.values())
    stop_processes(
        [proc for proc, _ in entries],
        logger,
        grace,
        labels={proc.pid: label for proc, label in entries},
    )
    with _registry_lock:
        _registry.clear()
