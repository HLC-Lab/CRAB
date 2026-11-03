import os
import re
import shlex
import subprocess
import threading
from typing import Any

from crab.core.execution.launcher import (
    MpirunLauncher,
    Placement,
    SrunLauncher,
    launch_line,
    launcher_for,
)
from crab.core.execution.settings import LauncherSpec
from crab.core.provenance import record_launch
from crab.log import CrabLogger

from .stop import register_process, stop_processes, unregister_process

# What an app's `env` key must look like to be exported from the app script.
_SHELL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The variables a rank's local index is read from in the NIC shim, per launcher. Under mpirun
# SLURM_LOCALID is never read: the batch step's value is inherited by every rank.
_SRUN_LOCAL_RANK_VARS: tuple[str, ...] = ("SLURM_LOCALID",)
_MPIRUN_LOCAL_RANK_VARS: tuple[str, ...] = (
    "OMPI_COMM_WORLD_LOCAL_RANK",
    "MPI_LOCALRANKID",
    "PMI_LOCAL_RANK",
    "PALS_LOCAL_RANKID",
)

# How much of an app's stderr is kept in memory: the last bytes, which is where a failure is
# reported from. The whole stream still goes to the bindings log under mpirun and srun.
STDERR_KEEP_BYTES = 4 * 1024 * 1024

# Tried in order inside the launcher script, on the compute node; the first that exists
# initialises the `module` command (Lmod sites often lack /etc/profile.d/modules.sh).
# `$LMOD_PKG/init/bash` is appended at run time. The setup wizard reads this same list to
# capture module lines (crab/setup/wizard.py).
MODULE_INIT_SCRIPTS: tuple[str, ...] = (
    "/etc/profile.d/modules.sh",
    "/etc/profile.d/lmod.sh",
    "/usr/share/lmod/lmod/init/bash",
)


def _rank_shim_text(
    job_id: int,
    nics: tuple[str, ...],
    launcher: object,
    gpus: tuple[int, ...] = (),
    nic_env: str = "UCX_NET_DEVICES",
    gpu_env: str = "CUDA_VISIBLE_DEVICES",
) -> str:
    """The text of the shim that gives each rank of app `job_id` its own NIC and GPU.

    A rank's NIC is `nics[local rank % len(nics)]`, set in `nic_env`, and its GPU
    `gpus[local rank % len(gpus)]`, set in `gpu_env` (with `CUDA_DEVICE_ORDER=PCI_BUS_ID` for CUDA, so the
    index is the PCI order). Either list may be empty. The local rank comes from the launcher's
    environment variables, and a rank that finds none fails instead of falling back to the first entry.
    Under the direct launcher there is one process, so the local rank is 0.
    """
    if isinstance(launcher, SrunLauncher):
        names = _SRUN_LOCAL_RANK_VARS
    elif isinstance(launcher, MpirunLauncher):
        names = _MPIRUN_LOCAL_RANK_VARS
    else:
        names = ()
    what = " and ".join(label for label, items in (("NIC", nics), ("GPU", gpus)) if items)
    lines = [
        "#!/bin/bash",
        f"# CRAB: each rank of app {job_id} uses its own {what} from the app's lists.",
    ]
    if nics:
        lines.append(f"nics=({' '.join(shlex.quote(nic) for nic in nics)})")
    if gpus:
        lines.append(f"gpus=({' '.join(str(gpu) for gpu in gpus)})")
    if names:
        # ${A:-${B:-${C:-}}}: the first of the variables that is set and not empty.
        chain = "".join(f"${{{name}:-" for name in names) + "}" * len(names)
        lines += [
            f"r={chain}",
            "case $r in",
            "    ''|*[!0-9]*)",
            f'        echo "crab: no local rank variable ({", ".join(names)}) set; '
            f'cannot pick a {what}" >&2',
            "        exit 1",
            "        ;;",
            "esac",
        ]
    else:
        lines.append("r=0")
    assigned = []
    if nics:
        lines.append(f'export {nic_env}="${{nics[$((10#$r % ${{#nics[@]}}))]}}"')
        assigned.append(f"{nic_env}=${nic_env}")
    if gpus:
        if gpu_env == "CUDA_VISIBLE_DEVICES":
            lines.append("export CUDA_DEVICE_ORDER=PCI_BUS_ID")
        lines.append(f'export {gpu_env}="${{gpus[$((10#$r % ${{#gpus[@]}}))]}}"')
        assigned.append(f"{gpu_env}=${gpu_env}")
    lines += [
        f'echo "crab: $(uname -n) local rank $r -> {" ".join(assigned)}" >&2',
        'exec "$@"',
    ]
    return "\n".join(lines) + "\n"


def run_job(
    job,
    spec: LauncherSpec,
    logger: CrabLogger,
    pre_commands: list[str] = None,
    live_stream: bool = False,
    data_path: str = None,
):
    """
    Launch an application process under the launcher `spec` describes, through a wrapper script,
    with `job.ppn` ranks on each of the job's hosts.

    A job that has `exp_dir` (the experiment directory, set by the runner for every run) gets its
    first launch recorded in the job directory's `crab_run.json`, and under mpirun or srun its
    stderr teed into `<exp_dir>/bindings_app_<id>.log` (the launch record is keyed by `config_key`,
    the app's key in the experiment's `apps`, else its id) with a `# run <n>` header when
    `run_number` is set. An app with `nics` is launched through a per-app shim script that sets
    each rank's `UCX_NET_DEVICES` to `nics[local rank % len(nics)]`; the launch record holds
    the list.
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
    # Slurm sets this from the batch script's --ntasks-per-node; the local scheduler scrubs SLURM_*.
    job_tasks = os.environ.get("SLURM_NTASKS_PER_NODE", "")
    job_ranks = int(job_tasks) if job_tasks.isascii() and job_tasks.isdigit() else None
    launcher = launcher_for(spec, extra_export=tuple(app_env))

    # The app script and the NIC shim live in the isolated run directory, to preserve provenance.
    run_dir = getattr(job, "run_dir", data_path)
    script_dir = os.path.join(run_dir, ".wrappers")
    os.makedirs(script_dir, exist_ok=True)
    nics = getattr(job, "nics", None)
    gpus = getattr(job, "gpus", None)
    if nics or gpus:
        # Absolute: srun runs the shim on every node.
        shim_path = os.path.abspath(os.path.join(script_dir, f"app_{job.id_num}_rank.sh"))
        with open(shim_path, "w") as f:
            f.write(
                _rank_shim_text(
                    job.id_num,
                    tuple(nics or ()),
                    launcher,
                    tuple(gpus or ()),
                    getattr(job, "nic_env", "UCX_NET_DEVICES"),
                    getattr(job, "gpu_env", "CUDA_VISIBLE_DEVICES"),
                )
            )
        app_command = f"bash {shlex.quote(shim_path)} {app_command}"

    cmd_string = launch_line(
        launcher,
        Placement(
            tuple(job.node_list),
            job.ppn,
            cpus=tuple(cpus) if cpus else None,
            bind_none=bool(getattr(job, "shared_cores", False)) and not cpus,
            co_run=getattr(job, "co_run", None),
            job_ranks_per_host=job_ranks or None,
        ),
        app_command,
    )

    # The binding report is the stderr of mpirun or srun, kept in the experiment directory: a run
    # directory that holds nothing else is deleted after its run, and with retain_files false all
    # of it is.
    exp_dir = getattr(job, "exp_dir", None)
    bindings_path = None
    if exp_dir and isinstance(launcher, (MpirunLauncher, SrunLauncher)):
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
                "ppn": job.ppn,
                "env": app_env,
                "nics": list(nics) if nics else None,
                "gpus": list(gpus) if gpus else None,
            },
        )

    # 2. Write the Execution Wrapper
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

    # stderr needs its own reader: a pipe holds about 64 KiB, and an app that writes more
    # (chatty UCX or CUDA) blocks on its write until CRAB reads, which would be after it exits.
    job.raw_stderr_buffer = bytearray()

    def _stderr_reader() -> None:
        cut = False
        try:
            for chunk in iter(lambda: process.stderr.read1(65536), b""):
                buf = job.raw_stderr_buffer
                buf += chunk
                if len(buf) > STDERR_KEEP_BYTES:
                    del buf[: len(buf) - STDERR_KEEP_BYTES]
                    for _ in range(3):  # the cut must not leave half a character at the start
                        if not buf or buf[0] & 0xC0 != 0x80:
                            break
                        del buf[0]
                    if not cut:
                        cut = True
                        logger.warning(
                            f"App {job.id_num}: stderr is over {STDERR_KEEP_BYTES} bytes, "
                            "keeping only the last of it"
                        )
        except (ValueError, OSError):
            pass  # Process killed or pipe closed

    job._stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    job._stderr_thread.start()

    logger.info(f"Launched App {job.id_num}  PID={process.pid}  nodes={job.node_list}")


def _collect_stderr(job: Any) -> bytes:
    """The app's stderr: the reader thread's buffer once the thread has drained the pipe.

    A job without a reader (not started by `run_job`) has its pipe read directly; that only
    returns once the write end is closed, so call it after the process has exited.
    """
    thread = getattr(job, "_stderr_thread", None)
    if thread:
        thread.join(timeout=2.0)
        return bytes(job.raw_stderr_buffer)
    try:
        return job.process.stderr.read()
    except OSError:
        return b""


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

    if job.process.poll() is None:
        # The stop could not end it: reading stderr or waiting would block on a live process.
        logger.warning(f"App {job.id_num}: still running, output not collected")
        job.set_output(b"".join(getattr(job, "raw_stdout_buffer", [])), b"")
        return

    out = b"".join(getattr(job, "raw_stdout_buffer", []))
    err = _collect_stderr(job)

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
    err = _collect_stderr(job)

    job.set_output(out, err)
    return False
