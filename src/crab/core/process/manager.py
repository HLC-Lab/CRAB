import os
import shlex
import signal
import subprocess
import threading
import time

from crab.log import CrabLogger

# Tried in order inside the launcher script, on the compute node; the first that exists
# initialises the `module` command (Lmod sites often lack /etc/profile.d/modules.sh).
# `$LMOD_PKG/init/bash` is appended at run time. The setup wizard reads this same list to
# capture module lines (crab/setup/wizard.py).
MODULE_INIT_SCRIPTS: tuple[str, ...] = (
    "/etc/profile.d/modules.sh",
    "/etc/profile.d/lmod.sh",
    "/usr/share/lmod/lmod/init/bash",
)


def run_job(
    job,
    wlmanager,
    ppn: int,
    logger: CrabLogger,
    pre_commands: list[str] = None,
    live_stream: bool = False,
    data_path: str = None,
    launcher: str = None,
):
    """
    Launch an application process via the workload manager using a physical execution wrapper.
    """
    if not job.node_list:
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")

    app_command = job.run_app()
    if not app_command or not str(app_command).strip():
        raise RuntimeError(
            f"Wrapper {type(job).__module__} (app {job.id_num}) returned an empty launch "
            "command; refusing to run it."
        )

    # 1. Get the base launcher command from the workload manager
    cmd_string = wlmanager.run_job(
        job.node_list,
        ppn,
        app_command,
        pre_commands=pre_commands,
        data_path=data_path,
        launcher=launcher,
    )

    # 2. Write the Execution Wrapper
    # Route the scripts into the isolated run directory to preserve provenance
    run_dir = getattr(job, "run_dir", data_path)
    script_dir = os.path.join(run_dir, ".wrappers")
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, f"app_{job.id_num}.sh")

    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        # Natively bring the cluster's module command to life
        candidates = " ".join(shlex.quote(path) for path in MODULE_INIT_SCRIPTS)
        f.write(f'for _crab_init in {candidates} ${{LMOD_PKG:+"$LMOD_PKG/init/bash"}}; do\n')
        f.write('    if [ -f "$_crab_init" ]; then\n')
        f.write('        source "$_crab_init"\n')
        f.write("        break\n")
        f.write("    fi\n")
        f.write("done\n\n")

        # Inject application-specific hooks cleanly
        if pre_commands:
            for cmd in pre_commands:
                f.write(f"{cmd}\n")

        f.write("\n# Execute workload\n")
        f.write(f"{cmd_string}\n")

    # 3. Execute the physical script as a clean subprocess.
    # start_new_session=True puts bash in its own process group so that
    # stop_process_groups() in end_job can reach srun/mpirun children as well.
    process = subprocess.Popen(
        ["bash", script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
        cwd=run_dir,
    )
    job.set_process(process)

    # Initialize the silent buffer
    job.raw_stdout_buffer = []

    # Define and start the silent reader thread
    def _silent_reader():
        try:
            for line in iter(process.stdout.readline, b""):
                job.raw_stdout_buffer.append(line)
        except (ValueError, OSError):
            pass  # Process killed or pipe closed

    job._stream_thread = threading.Thread(target=_silent_reader, daemon=True)
    job._stream_thread.start()

    logger.info(f"Launched App {job.id_num}  PID={process.pid}  nodes={job.node_list}")


def _warn(logger: CrabLogger | None, message: str) -> None:
    if logger is not None:
        logger.warning(message)


def _member_states(pgid: int) -> list[str]:
    """Kernel states of the processes in group `pgid`; empty when /proc cannot tell."""
    states: list[str] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return states
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                # "pid (comm) state ppid pgrp ...": comm may contain spaces and parentheses.
                fields = f.read().rpartition(")")[2].split()
            if int(fields[2]) == pgid:
                states.append(fields[0])
        except (OSError, IndexError, ValueError):
            continue
    return states


def _group_gone(process: subprocess.Popen, pgid: int, logger: CrabLogger | None) -> bool:
    """True once no live process is left in the group.

    The leader is reaped with poll() so it is not a zombie. A killed orphan member stays a
    zombie until its parent (init) reaps it, and killpg(pgid, 0) keeps succeeding for it (init
    never reaps inside a PID namespace such as the test sandbox), so a group whose members
    are all zombies counts as gone.
    """
    process.poll()
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        _warn(logger, f"Process group {pgid} is not ours to probe; assuming it is gone")
        return True
    states = _member_states(pgid)
    if states and all(state == "Z" for state in states):
        process.poll()  # the leader may have died since the poll above; reap it now
        return True
    return False


def _wait_gone(
    pending: dict[int, subprocess.Popen], timeout: float, logger: CrabLogger | None
) -> None:
    """Drop from `pending` the groups that are gone; stop waiting after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        for pgid in [g for g, p in pending.items() if _group_gone(p, g, logger)]:
            del pending[pgid]
        if not pending or time.monotonic() >= deadline:
            return
        time.sleep(0.05)


def stop_process_groups(
    processes: list[subprocess.Popen],
    grace: float = 5.0,
    logger: CrabLogger | None = None,
) -> list[int]:
    """Stop the process groups of `processes`: SIGTERM all, SIGKILL the survivors.

    Each process must be the leader of its own group (`start_new_session=True`), so its pid is
    the group id. All groups get SIGTERM at once and share one `grace` period; groups still
    alive after it get SIGKILL. A group id <= 1 or the caller's own group is never signalled.

    Returns:
        The group ids that needed SIGKILL.
    """
    own_group = os.getpgrp()
    pending: dict[int, subprocess.Popen] = {}
    for process in processes:
        if process.returncode is not None:
            # Reaped leader: bash ends only after its foreground srun/mpirun does, so nothing of
            # ours is left, and the group id may already belong to an unrelated process.
            continue
        pgid = process.pid
        if pgid <= 1 or pgid == own_group:
            _warn(logger, f"Refusing to signal process group {pgid} (not a group of its own)")
            continue
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            process.poll()  # already gone; reap the leader
            continue
        except PermissionError:
            _warn(logger, f"Process group {pgid} is not ours to signal; skipping")
            continue
        pending[pgid] = process

    _wait_gone(pending, grace, logger)

    killed: list[int] = []
    for pgid, process in list(pending.items()):
        _warn(logger, f"Process group {pgid} ignored SIGTERM for {grace}s; sending SIGKILL")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            del pending[pgid]
            process.poll()
            continue
        except PermissionError:
            _warn(logger, f"Process group {pgid} is not ours to signal; skipping")
            del pending[pgid]
            continue
        killed.append(pgid)

    # SIGKILL cannot be handled, so this only waits for the kernel to finish the teardown.
    _wait_gone(pending, 2.0, logger)
    for pgid in pending:
        _warn(logger, f"Process group {pgid} is still alive after SIGKILL")
    return killed


def end_job(job, logger: CrabLogger) -> None:
    """Stops a job's process group (SIGTERM, then SIGKILL after a grace) and retrieves output."""
    if not (hasattr(job, "process") and job.process):
        return

    # Stop the entire process group (bash wrapper + srun/mpirun children). SIGTERM first:
    # srun forwards it to its tasks and ends the Slurm step, mpirun forwards it to its ranks;
    # a SIGKILL would take srun down before it could do either and leave the step running.
    # SIGKILL stays as the fallback. A group-wide signal (not process.kill(), which only
    # reaches bash) also closes the stdout pipe the grandchildren hold open, which would
    # otherwise block communicate() indefinitely.
    stop_process_groups([job.process], logger=logger)

    # Let the stdout reader thread drain the last bytes and exit cleanly.
    if hasattr(job, "_stream_thread") and job._stream_thread:
        job._stream_thread.join(timeout=2.0)

    # Reconstruct stdout from the buffer; read any remaining stderr directly
    # (no concurrent reader on stderr, so this is safe and returns immediately
    # once the write-end is closed by the killed process group).
    out = b"".join(getattr(job, "raw_stdout_buffer", []))
    try:
        err = job.process.stderr.read()
    except OSError:
        err = b""

    # Reap the zombie so the OS can release the PID.
    job.process.wait()

    job.set_output(out, err)
    logger.debug(f"Killed App {job.id_num}")


def wait_timed(job, timeout_sec: float, logger: CrabLogger) -> bool:
    """Waits for a job with a timeout. Returns True if timed out."""
    try:
        job.process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        end_job(job, logger)
        return True

    # Join the silent reader thread to drain any remaining buffered bytes
    if hasattr(job, "_stream_thread") and job._stream_thread:
        job._stream_thread.join(timeout=2.0)

    out = b"".join(getattr(job, "raw_stdout_buffer", []))
    try:
        err = job.process.stderr.read()
    except OSError:
        err = b""

    job.set_output(out, err)
    return False
