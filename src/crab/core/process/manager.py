import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from crab.core.execution.launcher import MpirunLauncher, Placement, launch_line, launcher_for
from crab.core.execution.settings import LauncherSpec
from crab.core.provenance import record_launch
from crab.log import CrabLogger

# What an app's `env` key must look like to be exported from the app script.
_SHELL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# How long stop_processes waits after SIGTERM before it sends SIGKILL (ADR-034, "Cancel order").
STOP_GRACE_SECONDS = 10.0

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


def _signal_group(pgid: int, sig: int) -> None:
    """Send `sig` to process group `pgid`, a group CRAB started (its leader's pid).

    The pid-reuse argument: on Linux a pid number stays allocated while any process uses it as its
    group id, so a group that still has members cannot have its id handed to someone else. Callers
    signal a group only right after `_group_members` saw a live member (SIGTERM: while the leader
    is unreaped; SIGKILL: just before sending). On macOS the `killpg(pgid, 0)` probe means the same.
    """
    if not _usable_pgid(pgid):
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass  # already gone, or no longer ours to signal


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
        logger: receives one line per process whose group needed SIGKILL.
        grace: seconds to wait after SIGTERM before SIGKILL; STOP_GRACE_SECONDS when None.
        labels: optional pid -> name (such as "App 2") used in the log line instead of the pid.
    """
    if grace is None:
        grace = STOP_GRACE_SECONDS
    running = [proc for proc in procs if proc.poll() is None]
    pgids = [proc.pid for proc in running]
    for pgid in pgids:
        _signal_group(pgid, signal.SIGTERM)

    deadline = time.monotonic() + grace
    while True:
        for proc in running:
            proc.poll()  # reap an exited leader, so it is no longer a member
        if not any(_group_members(pgid) for pgid in pgids):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)

    for proc, pgid in zip(running, pgids, strict=True):
        # Re-check right before sending: the group may have emptied since the loop ended.
        if _group_members(pgid):
            name = (labels or {}).get(pgid, f"PID {pgid}")
            logger.warning(f"{name}: still running after {grace:g}s, sending SIGKILL")
            _signal_group(pgid, signal.SIGKILL)
        proc.wait()
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
    spec: LauncherSpec,
    ppn: int,
    logger: CrabLogger,
    pre_commands: list[str] = None,
    live_stream: bool = False,
    data_path: str = None,
):
    """
    Launch an application process under the launcher `spec` describes, through a wrapper script.

    A job that has `exp_dir` (the experiment directory, set by the runner for every run) gets its
    first launch recorded in the job directory's `crab_run.json`, and under mpirun its stderr
    teed into `<exp_dir>/bindings_app_<id>.log` (the launch record is keyed by `config_key`, the app's
    key in the experiment's `apps`, else its id) with a `# run <n>` header when `run_number` is set.
    """
    if not job.node_list:
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")

    app_command = job.run_app()
    if not app_command or not str(app_command).strip():
        raise RuntimeError(
            f"Wrapper {type(job).__module__} (app {job.id_num}) returned an empty launch "
            "command; refusing to run it."
        )

    app_env = dict(getattr(job, "app_env", None) or {})
    for name in app_env:
        if not _SHELL_NAME.fullmatch(name):
            raise ValueError(
                f"app {job.id_num}: env key {name!r} is not a valid shell variable name"
            )

    # 1. Build the launch line for the job's hosts, on the app's placed cores. An app on shared
    # cores without a core list is left unbound so co-located launches do not pile onto the
    # first cores.
    cpus = getattr(job, "cpu_list", None)
    launcher = launcher_for(spec, extra_export=tuple(app_env))
    cmd_string = launch_line(
        launcher,
        Placement(
            tuple(job.node_list),
            ppn,
            cpus=tuple(cpus) if cpus else None,
            bind_none=bool(getattr(job, "shared_cores", False)) and not cpus,
        ),
        app_command,
    )

    # The binding report is mpirun's stderr, kept in the experiment directory: a run directory
    # that holds nothing else is deleted after its run, and with retain_files false all of it is.
    exp_dir = getattr(job, "exp_dir", None)
    bindings_path = None
    if exp_dir and isinstance(launcher, MpirunLauncher):
        bindings_path = os.path.join(exp_dir, f"bindings_app_{job.id_num}.log")
    if exp_dir:
        record_launch(
            os.path.dirname(exp_dir),
            os.path.basename(exp_dir),
            str(getattr(job, "config_key", job.id_num)),
            {
                "line": cmd_string,
                "kind": spec.kind,
                "dialect": launcher.dialect if isinstance(launcher, MpirunLauncher) else None,
                "binding_report": bindings_path,
            },
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

        # The app's own variables: set here, so srun (`--export=ALL`) and direct inherit them and
        # mpirun passes them on with `-x`.
        for name, value in app_env.items():
            f.write(f"export {name}={shlex.quote(value)}\n")

        f.write("\n# Execute workload\n")
        if bindings_path:
            quoted = shlex.quote(bindings_path)
            run_number = getattr(job, "run_number", None)
            if run_number is not None:
                f.write(f'echo "# run {int(run_number)}" >> {quoted}\n')
            # The app's stderr still reaches CRAB: tee copies it to the log and back to stderr.
            f.write(f"{cmd_string} 2> >(tee -a {quoted} >&2)\n")
        else:
            f.write(f"{cmd_string}\n")

    # 3. Execute the physical script as a clean subprocess.
    # start_new_session=True puts bash in its own process group so that
    # stop_processes() in end_job can reach srun/mpirun children as well.
    process = subprocess.Popen(
        ["bash", script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
        cwd=run_dir,
    )
    register_process(process, f"App {job.id_num}")
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
    """Stops a job (SIGTERM first, SIGKILL after the grace) and retrieves output."""
    if not (hasattr(job, "process") and job.process):
        return

    # Stop the entire process group (bash wrapper + srun/mpirun children): srun is a grandchild
    # that inherits the stdout pipe and would keep it open, so signalling only bash is not
    # enough. The group gets SIGTERM, then SIGKILL if it outlives the grace.
    stop_processes([job.process], logger, labels={job.process.pid: f"App {job.id_num}"})

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
    logger.debug(f"Stopped App {job.id_num}")


def wait_timed(job, timeout_sec: float, logger: CrabLogger) -> bool:
    """Waits for a job with a timeout. Returns True if timed out."""
    try:
        job.process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        end_job(job, logger)
        return True
    unregister_process(job.process)

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
