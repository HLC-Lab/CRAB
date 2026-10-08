"""The local scheduler: a detached local subprocess instead of a cluster job.

Dev/testing-only (`CRAB_SCHEDULER=local`). A job is a `bash -c` running the worker, with a state
file under `<crab_root>/.crab_local_jobs/<pid>.json`; a later `crab status` is a fresh process, so
the finished state is read back from an exit-code file next to the job's logs.
"""

from __future__ import annotations

import datetime
import json
import os
import shlex
import signal
import subprocess
from pathlib import Path
from typing import Any

from crab.core.execution.scheduler.base import CancelResult, JobStatus, worker_command
from crab.log import CrabLogger


def _signal_target(pid: object) -> int | None:
    """The pid when it is safe to signal, else None.

    The state file is plain JSON, so its pid is untrusted: `killpg(1, ...)` is `kill(-1, ...)`
    (every process of the user), 0 and CRAB's own group hit CRAB itself, and a bool is an int.
    """
    if type(pid) is int and pid > 1 and pid != os.getpgrp():
        return pid
    return None


class LocalScheduler:
    """Runs CRAB jobs as detached local processes.

    Args:
        logger: receives the submit message.
        crab_root: the checkout root; job state files live in its `.crab_local_jobs/`.
    """

    def __init__(self, logger: CrabLogger, crab_root: str) -> None:
        self.log = logger
        self.crab_root = crab_root
        self._jobs_dir = Path(crab_root) / ".crab_local_jobs"

    def submit(self, job_dir: str, global_opts: dict[str, Any]) -> str | None:
        """Start the worker as a detached process and return its pid as the job id.

        stdout and stderr go to the same `slurm_output.log` / `slurm_error.log` names the Slurm
        job uses, so the log reader needs no special case. The command is wrapped in
        `bash -c '<cmd>; echo $? > <exit_file>'` because `crab status` cannot `wait()` on a
        process another invocation started.
        """
        exit_code_path = os.path.join(job_dir, "local_exit_code")
        full_cmd = f"{worker_command(job_dir)}; echo $? > {shlex.quote(exit_code_path)}"

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

        job_id = str(proc.pid)
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "pid": proc.pid,
            "data_dir": job_dir,
            "started_at": datetime.datetime.now().isoformat(),
        }
        with open(self._jobs_dir / f"{job_id}.json", "w") as f:
            json.dump(state, f)

        self.log.info(f"Submitted local job {job_id} (pid {proc.pid})")
        return job_id

    def knows(self, job_id: str) -> bool:
        """Whether a readable state file exists for this job id."""
        return self._read_state(job_id) is not None

    def status(self, ids: list[str]) -> list[JobStatus]:
        """The state of each job id, in input order. An id with no state file is UNKNOWN."""
        return [self._job_status(jid) for jid in ids]

    def cancel(self, job_id: str) -> CancelResult:
        """Signal the job's process group with SIGTERM.

        A finished job (no state file, or a state file with no pid) reports `cancelled=False`
        with a detail hint rather than raising. A recorded pid that is not a plausible job pid
        (see `_signal_target`) is never signalled and reports `cancelled=False` too.
        """
        state = self._read_state(job_id) or {}
        if "pid" not in state:
            return CancelResult(False, "local job already finished.")
        pid = _signal_target(state["pid"])
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

    def _read_state(self, job_id: str) -> dict[str, Any] | None:
        path = self._jobs_dir / f"{job_id}.json"
        if not path.is_file():
            return None
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _job_status(self, job_id: str) -> JobStatus:
        """Resolve one job from its exit-code file (finished) or PID liveness (running).

        The exit-code file is checked first: once it exists the job is finished, whether or not
        its PID has since been recycled by the OS.
        """
        state = self._read_state(job_id) or {}
        exit_code_path = Path(state.get("data_dir", "")) / "local_exit_code"
        if exit_code_path.is_file():
            code = exit_code_path.read_text().strip()
            return JobStatus(job_id, "COMPLETED" if code == "0" else "FAILED", "local", code)

        pid = _signal_target(state.get("pid"))
        if pid is not None:
            try:
                os.kill(pid, 0)
                return JobStatus(job_id, "RUNNING", "local")
            except OSError:
                pass

        return JobStatus(job_id, "UNKNOWN", "local")
