"""MPI dialect detection from `mpirun --version` output."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator

import pytest

from crab.core.execution.launcher import dialect
from crab.core.execution.launcher.dialect import parse_version_output, probe

OMPI4 = "mpirun (Open MPI) 4.1.8\n\nReport bugs to https://www.open-mpi.org/community/help/\n"
OPENRTE = "mpiexec (OpenRTE) 4.1.5\n\nReport bugs to https://www.open-mpi.org/community/help/\n"
OMPI5 = "mpirun (Open MPI) 5.0.11\n\nReport bugs to https://www.open-mpi.org/community/help/\n"
OMPI3 = "mpirun (Open MPI) 3.1.6\n\nReport bugs to http://www.open-mpi.org/community/help/\n"
HYDRA = (
    "HYDRA build details:\n"
    "    Version:                                 4.1.2\n"
    "    Release Date:                            Wed Jun  7 15:22:12 CDT 2023\n"
)
INTEL = (
    "Intel(R) MPI Library for Linux* OS, Version 2021.10 Build 20230619 (id: c2e19c2f3e)\n"
    "Copyright 2003-2023, Intel Corporation.\n"
)
STUB = (
    "This version of Open MPI was installed without the mpiexec/mpirun commands "
    "to avoid conflicts with the Slurm launcher; use srun instead.\n"
)


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    dialect.clear_cache()
    yield
    dialect.clear_cache()


def test_openmpi4() -> None:
    assert parse_version_output(OMPI4) == "openmpi4"


def test_openrte_naming_is_openmpi4() -> None:
    assert parse_version_output(OPENRTE) == "openmpi4"


def test_openmpi5() -> None:
    assert parse_version_output(OMPI5) == "openmpi5"


def test_hydra() -> None:
    assert parse_version_output(HYDRA) == "hydra"


def test_intel_is_refused_with_alternatives() -> None:
    with pytest.raises(ValueError) as err:
        parse_version_output(INTEL)
    message = str(err.value)
    assert "Intel MPI is not supported" in message
    assert "Open MPI 4/5" in message
    assert "MPICH" in message


def test_intel_wins_over_open_mpi_text() -> None:
    with pytest.raises(ValueError, match="Intel MPI is not supported"):
        parse_version_output(INTEL + "mpirun (Open MPI) 4.1.8\n")


def test_old_openmpi_is_unsupported() -> None:
    with pytest.raises(ValueError, match="unsupported Open MPI version 3.1.6"):
        parse_version_output(OMPI3)


def test_spack_stub_wants_srun() -> None:
    with pytest.raises(ValueError, match="stub.*srun"):
        parse_version_output(STUB, exit_code=2)


def test_nonzero_exit_names_code_and_output() -> None:
    with pytest.raises(ValueError) as err:
        parse_version_output("something broke\nsecond line\n", exit_code=3)
    assert "exit code 3" in str(err.value)
    assert "something broke" in str(err.value)


@pytest.mark.parametrize("text", ["", "   \n", "hello world\nnot a version\n"])
def test_garbage_names_the_output(text: str) -> None:
    with pytest.raises(ValueError, match="Cannot tell which MPI") as err:
        parse_version_output(text)
    first_line = text.strip().split("\n")[0]
    assert first_line in str(err.value)


def test_probe_caches_per_command() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[str, int]:
        calls.append(argv)
        return OMPI5, 0

    assert probe("mpirun", runner) == "openmpi5"
    assert probe("mpirun", runner) == "openmpi5"
    assert calls == [["mpirun", "--version"]]
    assert probe("/opt/mpiexec", runner) == "openmpi5"
    assert len(calls) == 2


def test_probe_missing_binary_names_command() -> None:
    def runner(argv: list[str]) -> tuple[str, int]:
        raise FileNotFoundError(argv[0])

    with pytest.raises(ValueError, match="/nope/mpirun"):
        probe("/nope/mpirun", runner)


def test_probe_failure_is_not_cached() -> None:
    answers = iter([("", 127), (OMPI4, 0)])

    def runner(argv: list[str]) -> tuple[str, int]:
        return next(answers)

    with pytest.raises(ValueError):
        probe("mpirun", runner)
    assert probe("mpirun", runner) == "openmpi4"


def test_real_runner_uses_timeout_and_merges_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=OMPI4, stderr=None)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert probe("mpirun") == "openmpi4"
    assert seen["argv"] == ["mpirun", "--version"]
    assert seen["timeout"] == 10
    assert seen["stdout"] == subprocess.PIPE
    assert seen["stderr"] == subprocess.STDOUT
    assert seen["text"] is True


def test_probe_timeout_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="did not answer"):
        probe("mpirun")


@pytest.mark.skipif(shutil.which("mpirun") is None, reason="no mpirun on PATH")
def test_real_mpirun_on_this_machine() -> None:
    out = subprocess.run(
        ["mpirun", "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    if "Open MPI) 5" not in out.stdout + out.stderr:
        pytest.skip("this mpirun is not Open MPI 5")
    assert probe("mpirun") == "openmpi5"
