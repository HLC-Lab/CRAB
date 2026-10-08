"""The local scheduler: a detached local subprocess instead of a cluster job.

Dev/testing-only (a preset with `scheduler: "local"`). A job is a `bash -c` running the worker.
Its id is `local-<n>` and its record (`local_state.JobRecord`: id, host, data_dir, supervisor pid,
creation time) lives under `$XDG_STATE_HOME/crab/jobs/`, outside the checkout. A later
`crab status` is a fresh process, so the finished state is read back from an exit-code file next
to the job's logs. A record written on another host is an error: its pid means nothing here.
"""

from __future__ import annotations

import datetime
import os
import shlex
import signal
import socket
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from crab.core.execution.scheduler import local_state
from crab.core.execution.scheduler.base import CancelResult, JobStatus, worker_command
from crab.core.execution.scheduler.local_state import JobRecord
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


class LocalScheduler:
    """Runs CRAB jobs as detached local processes.

    Args:
        logger: receives the submit message.
        crab_root: the checkout root (kept for the Scheduler interface; job records do not
            live in it).
        environ: where `XDG_STATE_HOME` is read from; the process environment by default.
    """

    def __init__(
        self, logger: CrabLogger, crab_root: str, environ: Mapping[str, str] | None = None
    ) -> None:
        self.log = logger
        self.crab_root = crab_root
        self._jobs_dir = local_state.jobs_dir(os.environ if environ is None else environ)

    def submit(self, job_dir: str, global_opts: dict[str, Any]) -> str | None:
        """Start the worker as a detached process and return its `local-<n>` job id.

        stdout and stderr go to the same `slurm_output.log` / `slurm_error.log` names the Slurm
        job uses, so the log reader needs no special case. The command is wrapped in
        `bash -c '<cmd>; echo $? > <exit_file>'` because `crab status` cannot `wait()` on a
        process another invocation started.
        """
        exit_code_path = os.path.join(job_dir, "local_exit_code")
        full_cmd = f"{worker_command(job_dir)}; echo $? > {shlex.quote(exit_code_path)}"

        job_id = local_state.allocate_id(self._jobs_dir)
        stdout_f = open(os.path.join(job_dir, "slurm_output.log"), "wb")
        stderr_f = open(os.path.join(job_dir, "slurm_error.log"), "wb")
        try:
            proc = subprocess.Popen(
                ["bash", "-c", full_cmd],
                stdout=stdout_f,
                stderr=stderr_f,
                start_new_session=True,
            )
        finally:
            stdout_f.close()
            stderr_f.close()

        local_state.write_record(
            self._jobs_dir,
            JobRecord(
                id=job_id,
                host=socket.gethostname(),
                data_dir=os.path.abspath(job_dir),
                supervisor_pid=proc.pid,
                created=datetime.datetime.now().isoformat(),
            ),
        )

        self.log.info(f"Submitted local job {job_id} (pid {proc.pid})")
        return job_id

    def status(self, ids: list[str]) -> list[JobStatus]:
        """The state of each job id, in input order. An id with no record is UNKNOWN.

        Raises:
            ValueError: an id that is not `local-<n>`, a record of the wrong shape, or a record
                written on another host.
        """
        return [self._job_status(jid) for jid in ids]

    def cancel(self, job_id: str) -> CancelResult:
        """Signal the job's process group with SIGTERM.

        A job whose exit-code file exists is never signalled: it has finished, and its pid may
        have been reused by an unrelated process group.

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
        """Resolve one job from its exit-code file (finished) or PID liveness (running).

        The exit-code file is checked first: once it exists the job is finished, whether or not
        its PID has since been recycled by the OS.
        """
        record = self._read_record(job_id)
        if record is None:
            return JobStatus(job_id, "UNKNOWN", "local")
        exit_code_path = _exit_code_path(record)
        if exit_code_path.is_file():
            code = exit_code_path.read_text().strip()
            return JobStatus(job_id, "COMPLETED" if code == "0" else "FAILED", "local", code)

        pid = _signal_target(record.supervisor_pid)
        if pid is not None:
            try:
                os.kill(pid, 0)
                return JobStatus(job_id, "RUNNING", "local")
            except OSError:
                pass

        return JobStatus(job_id, "UNKNOWN", "local")
