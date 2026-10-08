"""The detached supervisor of one local job: `python -m <this module> <job_dir> --lock-fd N`.

`LocalScheduler.submit` takes the job's liveness lock and hands the descriptor to this process,
which keeps it for its whole life (the kernel drops it when the process dies, however it dies).
It reads the job spec `local_job.json`, writes PENDING, takes the host locks of an exclusive job,
writes RUNNING, runs the worker, then writes `local_exit_code` and the final state, both
atomically. It imports only `local_state` before it installs its SIGTERM handler (the process
manager that holds `STOP_GRACE_SECONDS` is imported later, when the deadline is computed), so a
cancel that lands during start-up is not lost.

The worker runs in its own session, so the supervisor's group holds the supervisor alone. The
supervisor handles SIGTERM (ADR-034, "Cancel order"):
- while the job waits for its host locks it unwinds at once and writes CANCELLED;
- once the worker runs it forwards SIGTERM to the worker's group, waits for the worker to tear its
  apps down, SIGKILLs the worker's group after `STOP_GRACE_SECONDS + 15` seconds (the
  `CRAB_CANCEL_DEADLINE` environment variable overrides that, in seconds; tests use it), and then
  writes `local_exit_code` (the worker's code, `-9` when it was killed) and CANCELLED.

The supervisor also holds the write end of a pipe it never writes to; the worker holds the read
end (`CRAB_SUPERVISOR_FD`). When the supervisor dies, however it dies, the worker reads EOF and
stops itself, so no worker keeps its hosts after nobody locks them any more.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Any

from crab.core.execution.scheduler import local_state

SPEC_FILE = "local_job.json"
EXIT_CODE_FILE = "local_exit_code"
SUPERVISOR_FD_ENV = "CRAB_SUPERVISOR_FD"
DEADLINE_ENV = "CRAB_CANCEL_DEADLINE"
# The worker needs the apps' grace, and its own shutdown (the engine sleeps 2 s) on top of it.
WORKER_EXTRA_SECONDS = 15.0


def _read_spec(job_dir: Path) -> dict[str, Any]:
    """The validated job spec: `argv` (non-empty strings), `hosts` (strings), `exclusive` (bool)
    and `state_root` (absolute path).

    Raises:
        ValueError: the file is missing, not JSON, or has the wrong shape.
    """
    path = job_dir / SPEC_FILE
    try:
        spec = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read the job spec {path}: {exc}") from exc
    if not isinstance(spec, dict):
        raise ValueError(f"job spec {path} is not a JSON object")
    argv, hosts, root = spec.get("argv"), spec.get("hosts"), spec.get("state_root")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
        raise ValueError(f"job spec {path}: argv must be a non-empty list of strings")
    if not (isinstance(hosts, list) and all(isinstance(h, str) for h in hosts)):
        raise ValueError(f"job spec {path}: hosts must be a list of strings")
    if type(spec.get("exclusive")) is not bool:
        raise ValueError(f"job spec {path}: exclusive must be true or false")
    if not (isinstance(root, str) and os.path.isabs(root)):
        raise ValueError(f"job spec {path}: state_root must be an absolute path")
    return spec


def _write_exit_code(job_dir: Path, code: int) -> None:
    """Write `local_exit_code` so a reader sees no file or the whole number."""
    tmp = job_dir / f"{EXIT_CODE_FILE}.{os.getpid()}.tmp"
    try:
        tmp.write_text(str(code))
        os.replace(tmp, job_dir / EXIT_CODE_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def _fail(job_dir: Path, reason: str) -> int:
    """Report a job that could not run: FAILED when the data dir is usable, the reason on stderr."""
    try:
        local_state.write_job_state(job_dir, "FAILED")
    except OSError:
        pass
    print(f"crab local supervisor: {reason}", file=sys.stderr)
    return 1


class _Cancelled(BaseException):
    """Raised by the SIGTERM handler while the job waits for its host locks (no worker yet)."""


def _signal_worker_group(pid: object, sig: int) -> None:
    """Send `sig` to the worker's process group; refuses a pid that is no worker's group id.

    The worker is the supervisor's own unreaped child and a session leader, so its pid is its
    group id, and the id cannot belong to anyone else while the child is unreaped. A non-int, a pid
    of 1 or less (`killpg(1)` is `kill(-1)`: every process of the user, the 2026-10-08 incident) or
    the supervisor's own group is never signalled.
    """
    if type(pid) is not int or pid <= 1 or pid == os.getpgrp():
        return
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass  # already gone


def _cancel_deadline() -> float:
    """Seconds from the forwarded SIGTERM to the SIGKILL of the worker's group."""
    # Imported here, not at the top: it pulls in about 125 modules, and the SIGTERM handler must
    # be installed before any heavy import.
    from crab.core.process.manager import STOP_GRACE_SECONDS

    default = STOP_GRACE_SECONDS + WORKER_EXTRA_SECONDS
    try:
        value = float(os.environ.get(DEADLINE_ENV, default))
    except ValueError:
        return default
    return value if value > 0 else default


class _Run:
    """What the SIGTERM handler needs to know about the job's progress.

    `phase` is "setup" (reading the spec), "locking" (the only phase the handler unwinds), then
    "starting" (the worker is being started: a SIGTERM is remembered and forwarded as soon as the
    worker exists), "running" and "done".
    """

    def __init__(self) -> None:
        self.phase = "setup"
        self.cancel_requested = False
        self.worker: subprocess.Popen | None = None
        self.forwarded_at: float | None = None

    def forward(self) -> None:
        """Forward SIGTERM to the worker's group, once."""
        if self.worker is not None and self.forwarded_at is None and self.worker.returncode is None:
            self.forwarded_at = time.monotonic()
            _signal_worker_group(self.worker.pid, signal.SIGTERM)

    def on_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """The SIGTERM handler; a second SIGTERM changes nothing."""
        self.cancel_requested = True
        if self.worker is not None:
            self.forward()
        elif self.phase == "locking":
            # Python retries a blocking `flock` after a handler returns, so only raising ends it.
            self.phase = "done"
            raise _Cancelled


def _wait_for_worker(run: _Run, worker: subprocess.Popen) -> int | None:
    """Wait for the worker. After a forwarded SIGTERM, SIGKILL its group once the deadline has
    passed. Returns its exit code, or None when it was killed that way."""
    deadline = _cancel_deadline()
    while True:
        try:
            return worker.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            if run.forwarded_at is not None and time.monotonic() - run.forwarded_at >= deadline:
                _signal_worker_group(worker.pid, signal.SIGKILL)
                worker.wait()
                print(
                    f"crab local supervisor: the worker did not stop within {deadline:g}s; "
                    "killed its group (its apps may still be running)",
                    file=sys.stderr,
                )
                return None


def _finish(job_dir: Path, run: _Run, code: int | None) -> None:
    """Write the exit code and the final state of a job whose worker has ended.

    The job is cancelled when a SIGTERM was forwarded to its worker (`forwarded_at`) or the worker
    had to be killed (`code` is None, recorded as `-9`). A SIGTERM that arrived after the worker
    had exited is no cancel: the worker's own result stands. The cancelled job's state is written
    first, so status never shows FAILED (the worker's 143) for it; the normal order is exit code,
    then state.
    """
    if run.forwarded_at is not None or code is None:
        local_state.write_job_state(job_dir, "CANCELLED")
        _write_exit_code(job_dir, -9 if code is None else code)
        return
    _write_exit_code(job_dir, code)
    local_state.write_job_state(job_dir, "COMPLETED" if code == 0 else "FAILED")


def _run_worker(job_dir: Path, spec: dict[str, Any], run: _Run) -> int | None:
    """Start the worker in its own session with the supervisor pipe, and wait for it.

    The pipe's write end stays open in this process and is never written: its closing is the
    worker's signal that the supervisor is gone. Returns the worker's code (None: killed).
    """
    read_fd, write_fd = os.pipe()  # both non-inheritable; `pass_fds` hands `read_fd` over
    try:
        env = dict(os.environ)
        env[SUPERVISOR_FD_ENV] = str(read_fd)
        worker = subprocess.Popen(
            spec["argv"],
            stdin=subprocess.DEVNULL,
            cwd=job_dir,
            env=env,
            pass_fds=(read_fd,),
            start_new_session=True,
        )
        run.worker = worker
        run.phase = "running"
        if run.cancel_requested:
            run.forward()  # a SIGTERM that arrived while the worker was being started
        return _wait_for_worker(run, worker)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def main(argv: list[str]) -> int:
    """Run one local job to its end; return 0 when it ran or was cancelled, else 1."""
    if len(argv) != 3 or argv[1] != "--lock-fd" or not argv[2].isdigit():
        print("usage: supervisor <job_dir> --lock-fd <fd>", file=sys.stderr)
        return 2
    run = _Run()
    previous = signal.signal(signal.SIGTERM, run.on_sigterm)
    try:
        return _supervise(Path(argv[0]), int(argv[2]), run)
    finally:
        run.phase = "done"
        signal.signal(signal.SIGTERM, previous)


def _supervise(job_dir: Path, lock_fd: int, run: _Run) -> int:
    """The job's life: PENDING, host locks, RUNNING, the worker, the final state."""
    try:
        # The worker must never hold the liveness lock: only this process keeps it alive.
        os.set_inheritable(lock_fd, False)
        spec = _read_spec(job_dir)
    except (OSError, ValueError) as exc:
        return _fail(job_dir, str(exc))
    held: list[int] = []
    try:
        local_state.write_job_state(job_dir, "PENDING")
        run.phase = "locking"
        if run.cancel_requested:
            raise _Cancelled
        if spec["exclusive"]:
            held = local_state.acquire_hosts(Path(spec["state_root"]), spec["hosts"], blocking=True)
        run.phase = "starting"
        if run.cancel_requested:
            raise _Cancelled
        local_state.write_job_state(job_dir, "RUNNING")
        _finish(job_dir, run, _run_worker(job_dir, spec, run))
    except _Cancelled:
        # No worker was started: nothing ran, so there is no exit code.
        run.phase = "done"
        try:
            local_state.write_job_state(job_dir, "CANCELLED")
        except OSError as exc:
            return _fail(job_dir, f"job was cancelled but its state could not be written: {exc}")
    except (OSError, ValueError) as exc:
        return _fail(job_dir, f"job could not run: {exc}")
    finally:
        local_state.release_hosts(held)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
