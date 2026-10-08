"""The local scheduler: a detached supervisor process instead of a cluster job.

Dev/testing-only (a preset with `scheduler: "local"`). Each job runs under its own supervisor
(`scheduler/supervisor.py`), which holds the job's liveness lock, writes `local_state.json` and
`local_exit_code` in the job's data directory, and runs the worker in a session of its own. The job's id is `local-<n>` and
its record (`local_state.JobRecord`: id, host, data_dir, supervisor pid, creation time) lives under
`$XDG_STATE_HOME/crab/jobs/`, outside the checkout. A later `crab status` is a fresh process, so
the state is derived from the files plus the lock: a held lock means the job is alive. A record
written on another host is an error: its pid means nothing here.
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import crab
from crab.core.execution.hosts import LOCALHOST
from crab.core.execution.scheduler import local_state
from crab.core.execution.scheduler.base import CancelResult, JobStatus, worker_argv
from crab.core.execution.scheduler.local_state import JobRecord
from crab.core.execution.settings import from_json
from crab.log import CrabLogger

# The largest `pid_max` Linux allows (2**22). A larger value is no pid, and 2**31 and above make
# `os.kill` raise OverflowError before any system call.
_MAX_PID = 4194304


def _signal_target(pid: object) -> int | None:
    """The pid when it is safe to signal, else None.

    The record is plain JSON, so its pid is untrusted: `killpg(1, ...)` is `kill(-1, ...)`
    (every process of the user), 0 and CRAB's own group hit CRAB itself, a bool is an int, and a
    value above Linux's maximum `pid_max` (4194304) is no pid and may overflow the system call.
    """
    if type(pid) is int and 1 < pid <= _MAX_PID and pid != os.getpgrp():
        return pid
    return None


def _exit_code_path(record: JobRecord) -> Path:
    """Where the finished job's exit code is written, from its record."""
    return Path(record.data_dir) / "local_exit_code"


_FINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT")


def _supervisor_env() -> dict[str, str]:
    """The environment of the supervisor: ours, with the directory holding the imported `crab`
    package first on `PYTHONPATH`, so the supervisor runs this same code."""
    root = str(Path(crab.__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else root
    return env


class LocalScheduler:
    """Runs CRAB jobs as local processes, each under a detached supervisor.

    Args:
        logger: receives the submit message.
        crab_root: the checkout root (kept for the Scheduler interface; job records do not
            live in it).
        environ: where `XDG_STATE_HOME` is read from; the process environment by default.
        worker_argv: builds the worker's argv from the job directory; tests inject a short command.
    """

    def __init__(
        self,
        logger: CrabLogger,
        crab_root: str,
        environ: Mapping[str, str] | None = None,
        worker_argv: Callable[[str], list[str]] = worker_argv,
    ) -> None:
        self.log = logger
        self.crab_root = crab_root
        self._state_root = Path(
            os.path.abspath(local_state.state_dir(os.environ if environ is None else environ))
        )
        self._jobs_dir = self._state_root / "jobs"
        self._worker_argv = worker_argv

    def submit(self, job_dir: str, global_opts: dict[str, Any]) -> str | None:
        """Start the job's supervisor, detached, and return its `local-<n>` job id.

        The job spec `local_job.json` tells the supervisor what to run and which hosts to lock.
        The liveness lock is taken here, before the supervisor exists, and its descriptor is
        handed over, so a job that was just submitted never reads LOST. stdout and stderr go to
        the `slurm_output.log` / `slurm_error.log` names the Slurm job uses, so the log reader
        needs no special case; the supervisor and the worker share them.

        Raises:
            ValueError: `execution.json` is invalid, or the record could not be written (the
                supervisor is terminated first; the message names the state directory).
        """
        job_dir = os.path.abspath(job_dir)  # the supervisor runs the worker with cwd=job_dir
        hosts, exclusive = self._hosts_used(job_dir, int(global_opts.get("numnodes", 1)))
        job_id = local_state.allocate_id(self._jobs_dir)
        spec = {
            "id": job_id,
            "argv": self._worker_argv(job_dir),
            "hosts": hosts,
            "exclusive": exclusive,
            "state_root": str(self._state_root),
        }
        (Path(job_dir) / "local_job.json").write_text(json.dumps(spec))

        fd = self._take_liveness_lock(job_id)
        try:
            with (
                open(os.path.join(job_dir, "slurm_output.log"), "ab") as stdout_f,
                open(os.path.join(job_dir, "slurm_error.log"), "ab") as stderr_f,
            ):
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "crab.core.execution.scheduler.supervisor",
                        job_dir,
                        "--lock-fd",
                        str(fd),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_f,
                    stderr=stderr_f,
                    start_new_session=True,
                    pass_fds=(fd,),
                    env=_supervisor_env(),
                )
        finally:
            os.close(fd)  # the supervisor's copy keeps the lock

        try:
            local_state.write_record(
                self._jobs_dir,
                JobRecord(
                    id=job_id,
                    host=socket.gethostname(),
                    data_dir=job_dir,
                    supervisor_pid=proc.pid,
                    created=datetime.datetime.now().isoformat(),
                ),
            )
        except (OSError, ValueError) as exc:
            try:
                # The supervisor is our own child and a session leader. It forwards the SIGTERM
                # to its worker, which has a group of its own.
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Still stopping its worker: its death is enough, the worker sees the pipe close.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            raise ValueError(
                f"could not write the record of local job {job_id} in {self._jobs_dir}: {exc}"
            ) from exc

        self.log.info(f"Submitted local job {job_id} (pid {proc.pid})")
        return job_id

    def _hosts_used(self, job_dir: str, numnodes: int) -> tuple[list[str], bool]:
        """The host names a job locks (the first `numnodes` of `execution.json`) and `exclusive`.

        Without an `execution.json` the job uses `localhost`, exclusively.
        """
        path = Path(job_dir) / "execution.json"
        if not path.is_file():
            return [LOCALHOST], True
        try:
            settings = from_json(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        return [host.name for host in settings.hosts[:numnodes]], settings.exclusive

    def _take_liveness_lock(self, job_id: str) -> int:
        """Take the job's liveness lock, retrying for up to a second: a status probe holds the
        lock for an instant."""
        deadline = time.monotonic() + 1.0
        while True:
            try:
                return local_state.open_liveness_lock(self._jobs_dir, job_id)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def status(self, ids: list[str]) -> list[JobStatus]:
        """The state of each job id, in input order. An id with no record is UNKNOWN.

        Raises:
            ValueError: an id that is not `local-<n>`, a record of the wrong shape, or a record
                written on another host.
        """
        return [self._job_status(jid) for jid in ids]

    def cancel(self, job_id: str) -> CancelResult:
        """Signal the supervisor's process group with SIGTERM, while its liveness lock is held.

        The supervisor's group holds the supervisor alone: the supervisor forwards the SIGTERM to
        the worker, which stops its apps (SIGTERM, then SIGKILL after a grace), and then writes
        `local_exit_code` and the state `CANCELLED`. A job still waiting for its host locks ends
        `CANCELLED` too. The call returns at once; the job stops some seconds later.

        A job whose exit-code file exists is never signalled: it has finished. A job whose lock
        is free is never signalled either: its supervisor is gone and the recorded pid may have
        been reused by an unrelated process group.

        A finished job reports `cancelled=False` with a detail hint rather than raising, and so
        does an id with no record ("no such local job"). A recorded pid that is not a plausible
        job pid (see `_signal_target`) is never signalled and reports `cancelled=False` too.

        Raises:
            ValueError: an id that is not `local-<n>`, a record of the wrong shape, or a record
                written on another host.
        """
        record = self._read_record(job_id)
        if record is None:
            return CancelResult(False, "no such local job")
        if _exit_code_path(record).is_file():
            return CancelResult(False, "local job already finished.")
        if not local_state.is_alive(self._jobs_dir, job_id):
            return CancelResult(
                False, "local job is not running (its supervisor is gone); nothing was signalled."
            )
        pid = _signal_target(record.supervisor_pid)
        if pid is None:
            return CancelResult(False, "local job state has no valid pid; nothing was signalled.")
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return CancelResult(False, "local job already finished.")
        except OSError as exc:
            return CancelResult(False, f"could not cancel local job: {exc}")
        return CancelResult(True, None)

    def node_list(self) -> list[str]:
        """The only node of a local job."""
        return ["localhost"]

    def describe_nodes(self) -> dict[str, Any]:
        """The local machine as the cluster's one node."""
        return {"available": True, "partitions": [], "nodes": ["localhost"]}

    def _read_record(self, job_id: str) -> JobRecord | None:
        """The job's record, or None when there is none; refuses a record from another host."""
        record = local_state.read_record(self._jobs_dir, job_id)
        if record is not None:
            local_state.check_host(record, socket.gethostname())
        return record

    def _job_status(self, job_id: str) -> JobStatus:
        """Resolve one job from the lock, the exit-code file and `local_state.json`.

        The lock is probed first: once it is free every write of the supervisor is done, so the
        files read afterwards are final. A final state in `local_state.json` (COMPLETED, FAILED,
        CANCELLED, TIMEOUT) wins, with the exit code from the exit-code file when there is one
        (a cancelled job has both). Otherwise the exit-code file decides COMPLETED or FAILED (the
        supervisor wrote it a moment before the final state); a live job reports its state file
        (PENDING before the supervisor wrote one); a dead job with neither is `LOST`.
        """
        record = self._read_record(job_id)
        if record is None:
            return JobStatus(job_id, "UNKNOWN", "local")
        alive = local_state.is_alive(self._jobs_dir, job_id)
        exit_code_path = _exit_code_path(record)
        code = exit_code_path.read_text().strip() if exit_code_path.is_file() else None
        try:
            state = local_state.read_job_state(Path(record.data_dir))
        except ValueError:
            if code is None:
                raise
            state = None  # an unreadable state file never hides a finished job
        if state in _FINAL_STATES:
            return JobStatus(job_id, state, "local", code)
        if code is not None:
            return JobStatus(job_id, "COMPLETED" if code == "0" else "FAILED", "local", code)
        if alive:
            return JobStatus(job_id, state or "PENDING", "local")
        return JobStatus(job_id, "LOST", "local")
