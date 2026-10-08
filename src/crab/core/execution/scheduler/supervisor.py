"""The detached supervisor of one local job: `python -m <this module> <job_dir> --lock-fd N`.

`LocalScheduler.submit` takes the job's liveness lock and hands the descriptor to this process,
which keeps it for its whole life (the kernel drops it when the process dies, however it dies).
It reads the job spec `local_job.json`, writes PENDING, takes the host locks of an exclusive job,
writes RUNNING, runs the worker, then writes `local_exit_code` and the final state, both
atomically. Pure standard library apart from `local_state`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from crab.core.execution.scheduler import local_state

SPEC_FILE = "local_job.json"
EXIT_CODE_FILE = "local_exit_code"


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


def main(argv: list[str]) -> int:
    """Run one local job to its end; return 0 when it ran (whatever the worker's code), else 1."""
    if len(argv) != 3 or argv[1] != "--lock-fd" or not argv[2].isdigit():
        print("usage: supervisor <job_dir> --lock-fd <fd>", file=sys.stderr)
        return 2
    job_dir = Path(argv[0])
    try:
        # The worker must never hold the liveness lock: only this process keeps it alive.
        os.set_inheritable(int(argv[2]), False)
        spec = _read_spec(job_dir)
    except (OSError, ValueError) as exc:
        return _fail(job_dir, str(exc))
    held: list[int] = []
    try:
        local_state.write_job_state(job_dir, "PENDING")
        if spec["exclusive"]:
            held = local_state.acquire_hosts(Path(spec["state_root"]), spec["hosts"], blocking=True)
        local_state.write_job_state(job_dir, "RUNNING")
        code = subprocess.Popen(spec["argv"], stdin=subprocess.DEVNULL, cwd=job_dir).wait()
        _write_exit_code(job_dir, code)
        local_state.write_job_state(job_dir, "COMPLETED" if code == 0 else "FAILED")
    except (OSError, ValueError) as exc:
        return _fail(job_dir, f"job could not run: {exc}")
    finally:
        local_state.release_hosts(held)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
