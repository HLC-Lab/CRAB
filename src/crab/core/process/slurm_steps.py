import math
import os
import subprocess
import time

from crab.log import CrabLogger

# Steps that belong to the job itself, never to an app.
_JOB_STEPS = {"batch", "extern", "interactive"}
_COMMAND_TIMEOUT = 15
_POLL_INTERVAL = 0.5


def _list_app_steps(job: str) -> list[str]:
    """Ids (`<job>.<step>`) of the app steps of `job` that squeue still lists.

    Raises:
        OSError: squeue is missing or cannot be started.
        subprocess.SubprocessError: squeue timed out or exited non-zero.
    """
    result = subprocess.run(
        ["squeue", "--steps", "--job", job, "--noheader", "--format=%i"],
        text=True,
        capture_output=True,
        timeout=_COMMAND_TIMEOUT,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(f"squeue exited with {result.returncode}")

    own_step = os.environ.get("SLURM_STEP_ID")
    steps: list[str] = []
    for line in result.stdout.splitlines():
        step_id = line.strip()
        prefix, _, suffix = step_id.partition(".")
        if prefix != job or not suffix or suffix in _JOB_STEPS or suffix == own_step:
            continue
        steps.append(step_id)
    return steps


def cancel_leftover_steps(logger: CrabLogger, settle: float = 5.0) -> list[str]:
    """Cancel the app steps of the current Slurm job that are still running, with a warning.

    Meant for after a run's apps were stopped: a step that survived the stop (its tasks ignored
    SIGTERM, or its srun was killed) would keep its nodes busy and block later steps. Outside a
    Slurm job (no SLURM_JOB_ID) it does nothing. The batch and extern steps and the step this
    process runs in (SLURM_STEP_ID) are never touched.

    Steps just stopped need a moment to leave squeue, so a step is only cancelled when it is
    still listed after `settle` seconds. Failures of squeue or scancel become warnings.

    Returns:
        The ids of the steps that were cancelled.
    """
    job = os.environ.get("SLURM_JOB_ID")
    if not job:
        return []

    try:
        leftovers = _list_app_steps(job)
        if leftovers:
            for _ in range(math.ceil(settle / _POLL_INTERVAL)):
                time.sleep(_POLL_INTERVAL)
                leftovers = _list_app_steps(job)
                if not leftovers:
                    break
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Could not list the Slurm steps of job {job}: {exc}")
        return []

    cancelled: list[str] = []
    for step_id in leftovers:
        try:
            result = subprocess.run(
                ["scancel", step_id], text=True, capture_output=True, timeout=_COMMAND_TIMEOUT
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                f"Slurm step {step_id} was still running and could not be cancelled: {exc}"
            )
            continue
        if result.returncode != 0:
            logger.warning(
                f"Slurm step {step_id} was still running and scancel failed "
                f"(exit {result.returncode}): {result.stderr.strip()}"
            )
            continue
        logger.warning(
            f"Slurm step {step_id} was still running after its app stopped; cancelled it"
        )
        cancelled.append(step_id)
    return cancelled
