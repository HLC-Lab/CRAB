"""Real mpirun launches through CRAB's own launcher, against every MPI found on the machine.

The binaries come from `CRAB_TEST_MPIRUNS` (a `:`-separated list of mpirun paths) or, when it is
unset, from `mpirun` on PATH. Each binary runs with the syntax of its own detected dialect. The
two-host case fakes its hosts with `tests/fake_ssh`, handed to the MPI as its remote agent.

Run alone with `pytest -m mpi tests/test_mpi_real.py -rs`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from crab.core.execution.launcher import Placement, launch_line, launcher_for
from crab.core.execution.launcher import dialect as dialect_module
from crab.core.execution.settings import LauncherSpec

pytestmark = pytest.mark.mpi

FAKE_SSH = Path(__file__).parent / "fake_ssh"
LAUNCH_TIMEOUT_SECONDS = 60
# What a rank prints: its number, from whichever launcher variable is set.
RANK_CODE = (
    "import os;print('rank='+(os.environ.get('OMPI_COMM_WORLD_RANK')"
    " or os.environ.get('PMI_RANK') or os.environ.get('PMIX_RANK') or '?'))"
)


def _agent_flags(dialect: str, agent: Path) -> tuple[str, ...]:
    """The flags that make the MPI start its remote daemons through `agent` instead of ssh."""
    path = shlex.quote(str(agent))
    if dialect == "openmpi4":
        return ("--mca", "plm_rsh_agent", path)
    if dialect == "openmpi5":
        return ("--prtemca", "plm_ssh_agent", path)
    if dialect == "hydra":
        return ("-launcher", "ssh", "-launcher-exec", path)
    raise AssertionError(f"no agent flags for dialect {dialect!r}")


def _discover() -> list[object]:
    """One pytest param per mpirun found: its dialect in the id, a skip mark when unusable."""
    listed = os.environ.get("CRAB_TEST_MPIRUNS", "")
    if listed:
        paths = [item for item in listed.split(":") if item]
    else:
        found = shutil.which("mpirun")
        paths = [found] if found else []
    if not paths:
        reason = (
            "no mpirun to test: set CRAB_TEST_MPIRUNS to a ':'-separated list of mpirun paths "
            "or put mpirun on PATH"
        )
        return [pytest.param(None, marks=pytest.mark.skip(reason=reason), id="no-mpirun")]
    params: list[object] = []
    for path in paths:
        name = os.path.basename(path)
        try:
            found_dialect = dialect_module.probe(path)
        except ValueError as error:
            skip = pytest.mark.skip(reason=f"{path}: {error}")
            params.append(pytest.param(path, marks=skip, id=f"{name}-unusable"))
        else:
            params.append(pytest.param(path, id=f"{name}-{found_dialect}"))
    return params


MPIRUNS = _discover()


def _launch_line(mpirun: str, hosts: tuple[str, ...], agent: Path | None) -> str:
    """The launch line for 2 ranks over `hosts`, each rank printing its number.

    `agent` is the fake ssh; without it the MPI starts its own daemons.
    """
    flags = _agent_flags(dialect_module.probe(mpirun), agent) if agent is not None else ()
    spec = LauncherSpec("mpirun", (shlex.quote(mpirun),), "auto", flags, ())
    launcher = launcher_for(spec, environ={})
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(RANK_CODE)}"
    return launch_line(launcher, Placement(hosts, 2 // len(hosts)), command)


def _run(line: str) -> tuple[int, str, str]:
    """Run a launch line in its own process group; on timeout, stop that group (our own)."""
    process = subprocess.Popen(
        ["bash", "-c", line],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=LAUNCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
        pytest.fail(f"launch timed out: {line}\nstdout: {out}\nstderr: {err}")
    return process.returncode, out, err


def _ranks(stdout: str) -> list[str]:
    return sorted(line.split("=", 1)[1] for line in stdout.splitlines() if line.startswith("rank="))


@pytest.mark.parametrize("mpirun", MPIRUNS)
def test_single_host_two_ranks_report_rank_0_and_1(mpirun: str) -> None:
    line = _launch_line(mpirun, ("localhost",), None)

    code, out, err = _run(line)

    assert code == 0, f"{line}\nstdout: {out}\nstderr: {err}"
    assert _ranks(out) == ["0", "1"], f"{line}\nstdout: {out}\nstderr: {err}"


@pytest.mark.parametrize("mpirun", MPIRUNS)
def test_two_fake_hosts_launch_one_daemon_each(mpirun: str, tmp_path: Path) -> None:
    agent = tmp_path / "fake_ssh"
    shutil.copy2(FAKE_SSH, agent)
    line = _launch_line(mpirun, ("nodea", "nodeb"), agent)

    code, out, err = _run(line)

    assert code == 0, f"{line}\nstdout: {out}\nstderr: {err}"
    assert _ranks(out) == ["0", "1"], f"{line}\nstdout: {out}\nstderr: {err}"
    log = (tmp_path / "fake_ssh.log").read_text().splitlines()
    assert sorted(entry.split(":", 1)[0] for entry in log) == ["nodea", "nodeb"], "\n".join(log)


def _bound_to(output: str) -> list[str]:
    """What the binding report says each rank is bound to.

    Open MPI prints `... bound to <cores>` on its standard error; Hydra, when the test sets
    HYDRA_TOPO_DEBUG=1 (CRAB's launcher never does: the lines go to stdout), prints
    `process <n> binding: <one 0/1 per core>` on its standard output.
    """
    bound = []
    for line in output.lower().splitlines():
        if "bound to" in line:
            bound.append(line.split("bound to", 1)[1].strip())
        elif line.startswith("process ") and " binding: " in line:
            bound.append(line.split(" binding: ", 1)[1].strip())
    return bound


@pytest.mark.parametrize("mpirun", MPIRUNS)
def test_two_concurrent_launches_on_different_cores_are_bound_to_different_cores(
    mpirun: str,
) -> None:
    cores = sorted(os.sched_getaffinity(0))
    if len(cores) < 2:
        pytest.skip("two cores are needed to place two apps on different ones")
    spec = LauncherSpec("mpirun", (shlex.quote(mpirun),), "auto", (), ())
    launcher = launcher_for(spec, environ={})
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(1)')}"
    lines = [
        launch_line(launcher, Placement(("localhost",), 1, cpus=(core,)), command)
        for core in cores[:2]
    ]
    # Hydra only reports bindings on request, and CRAB's launcher does not ask: the test does.
    environment = dict(os.environ, HYDRA_TOPO_DEBUG="1")
    processes = [
        subprocess.Popen(
            ["bash", "-c", line],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environment,
        )
        for line in lines
    ]
    reports: list[str] = []
    for process, line in zip(processes, lines, strict=True):
        try:
            out, err = process.communicate(timeout=LAUNCH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            for running in processes:
                if running.poll() is None:
                    os.killpg(running.pid, signal.SIGKILL)
            pytest.fail(f"launch timed out: {line}")
        assert process.returncode == 0, f"{line}\nstdout: {out}\nstderr: {err}"
        reports.append(f"{out}\n{err}")

    bound = [_bound_to(report) for report in reports]
    shown = "\n".join(f"{line}\n{report}" for line, report in zip(lines, reports, strict=True))
    assert all(len(found) == 1 for found in bound), f"no binding report line:\n{shown}"
    assert bound[0] != bound[1], f"both ranks bound alike:\n{shown}"
