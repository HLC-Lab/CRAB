import os
import signal
import subprocess
import threading

from crab.log import CrabLogger


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

    # 1. Get the base launcher command from the workload manager
    cmd_string = wlmanager.run_job(
        job.node_list,
        ppn,
        job.run_app(),
        pre_commands=pre_commands,
        data_path=data_path,
        launcher=launcher,
    )

    if not cmd_string:
        cmd_string = "echo a > /dev/null"

    # 2. Write the Execution Wrapper
    # Route the scripts into the isolated run directory to preserve provenance
    run_dir = getattr(job, "run_dir", data_path)
    script_dir = os.path.join(run_dir, ".wrappers")
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, f"app_{job.id_num}.sh")

    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        # Natively bring the cluster's module command to life
        f.write("if [ -f /etc/profile.d/modules.sh ]; then\n")
        f.write("    source /etc/profile.d/modules.sh\n")
        f.write("fi\n\n")

        # Inject application-specific hooks cleanly
        if pre_commands:
            for cmd in pre_commands:
                f.write(f"{cmd}\n")

        f.write("\n# Execute workload\n")
        f.write(f"{cmd_string}\n")

    # 3. Execute the physical script as a clean subprocess.
    # start_new_session=True puts bash in its own process group so that
    # os.killpg() in end_job can reach srun/mpirun children as well.
    process = subprocess.Popen(
        ["bash", script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
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


def end_job(job, logger: CrabLogger) -> None:
    """Forcefully terminates a job and retrieves output."""
    if not (hasattr(job, "process") and job.process):
        return

    # Kill the entire process group (bash wrapper + srun/mpirun children).
    # process.kill() only delivers SIGKILL to bash; srun is a grandchild that
    # inherits the stdout pipe and keeps it open, causing communicate() to block
    # indefinitely. os.killpg() reaches every process in the group at once.
    try:
        os.killpg(os.getpgid(job.process.pid), signal.SIGKILL)
    except OSError:
        pass  # already dead

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
