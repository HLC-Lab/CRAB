"""Characterization tests for the Slurm code path, with expected values written out as literals.

They pin the full `crab_job.sh` the orchestrator writes (header merge rules, venv and system
setup lines, worker command), the `sbatch` call and its failure modes, the `srun`/`mpirun` launch
strings of the Slurm workload manager, and the `squeue`/`sacct`/`scancel` parsing of the status
and cancel contract. Their job is to keep this behavior byte-identical while the Slurm code moves
into `core/execution/`: only the fixtures below may change with the move, never an expected value.
Some pinned outputs look odd (marked "as it is today"); they are recorded, not endorsed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.cli import contract
from crab.core import engine as engine_module
from crab.core.engine import Engine
from crab.core.execution.launcher import Placement, launch_line, launcher_for
from crab.core.process import manager as process_manager

_LAUNCH_ENV_VARS = (
    "CRAB_MPIRUN",
    "CRAB_MPIRUN_ADDITIONAL_FLAGS",
    "CRAB_MPIRUN_MAP_BY_NODE_FLAG",
    "CRAB_PINNING_FLAGS",
)

_FIXED_PYTHON = "/opt/venv/bin/python"
_FIXED_CRAB = "/opt/crab/bin/crab"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def crab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty checkout root. CRAB_ROOT is the one module attribute patched: the `source`
    line of the job script depends on it. Add `.venv/bin/activate` to get that line."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(engine_module, "CRAB_ROOT", str(root))
    monkeypatch.setattr(sys, "executable", _FIXED_PYTHON)
    monkeypatch.setattr(sys, "argv", [_FIXED_CRAB])
    return root


def _add_venv(root: Path) -> None:
    activate = root / ".venv" / "bin" / "activate"
    activate.parent.mkdir(parents=True)
    activate.write_text("")


class _FakeSbatch:
    """Stands in for `subprocess.check_output`; records every call."""

    def __init__(self, output: str = "Submitted batch job 4242") -> None:
        self.output = output
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> str:
        self.calls.append((list(cmd), kwargs))
        return self.output


@pytest.fixture
def sbatch(monkeypatch: pytest.MonkeyPatch) -> _FakeSbatch:
    fake = _FakeSbatch()
    monkeypatch.setattr(subprocess, "check_output", fake)
    return fake


def _make_engine() -> Engine:
    return Engine(logger=MagicMock())


def _config(tmp_path: Path, **global_options: Any) -> dict[str, Any]:
    options: dict[str, Any] = {"datapath": str(tmp_path / "data")}
    options.update(global_options)
    return {"global_options": options, "experiments": {"ex1": {"apps": {}}}}


def _submit(tmp_path: Path, crab_root: Path, **global_options: Any) -> tuple[dict[str, Any], str]:
    """Submit through Engine.run in orchestrator mode. Returns the result dict and the script
    text with the per-run data dir and the checkout root replaced by placeholders."""
    config = _config(tmp_path, **global_options)
    result = _make_engine().run(config, {"CRAB_SYSTEM": "testsys"}, is_worker=False)
    data_dir = result["data_dir"]
    text = (Path(data_dir) / "crab_job.sh").read_text()
    return result, text.replace(data_dir, "<DATA>").replace(str(crab_root), "<ROOT>")


_WORKER_LINE = "/opt/venv/bin/python /opt/crab/bin/crab worker --workdir <DATA>\n"


# --------------------------------------------------------------------------- #
# crab_job.sh
# --------------------------------------------------------------------------- #
def test_script_minimal(tmp_path, crab_root, sbatch):
    _, script = _submit(
        tmp_path, crab_root, numnodes=2, ppn=4, extrainfo="bench", walltime="00:30:00"
    )
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_bench\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:30:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "\n" + _WORKER_LINE
    )


def test_script_defaults_without_extrainfo_walltime_or_ppn(tmp_path, crab_root, sbatch):
    _, script = _submit(tmp_path, crab_root, numnodes=1)
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_job\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:10:00\n"
        "#SBATCH --nodes=1\n"
        "#SBATCH --ntasks-per-node=1\n"
        "\n" + _WORKER_LINE
    )


def test_script_preset_defaults_venv_and_system_header(tmp_path, crab_root, sbatch):
    _add_venv(crab_root)
    _, script = _submit(
        tmp_path,
        crab_root,
        numnodes=2,
        ppn=4,
        extrainfo="bench",
        system_sbatch=["--partition=boost", "--account=proj", "--nodes=9"],
        system_header=["module load gcc", "module load openmpi", "export X=1\nrm -rf /"],
    )
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_bench\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:10:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "#SBATCH --partition=boost\n"
        "#SBATCH --account=proj\n"
        "\n"
        "source <ROOT>/.venv/bin/activate\n"
        "\n"
        "# --- System Setup (Modules & Environment) ---\n"
        "module load gcc\n"
        "module load openmpi\n"
        "\n" + _WORKER_LINE
    )


def test_script_user_list_directives_override_preset(tmp_path, crab_root, sbatch):
    _, script = _submit(
        tmp_path,
        crab_root,
        numnodes=2,
        ppn=4,
        extrainfo="bench",
        system_sbatch=["--partition=boost", "--account=proj", "--qos=normal"],
        sbatch_directives=[
            "--partition=dev",
            "--ntasks-per-node=99",
            "-N 3",
            "--output=/custom/out.log",
            "--exclusive",
            "--qos=low",
            "--qos=high",
            "--mem=8G",
        ],
    )
    # --partition and --qos keep the position of their first definition; --nodes and
    # --ntasks-per-node stay CRAB's; --output keeps its slot with the user's value.
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_bench\n"
        "#SBATCH --output=/custom/out.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:10:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "#SBATCH --partition=dev\n"
        "#SBATCH --account=proj\n"
        "#SBATCH --qos=high\n"
        "#SBATCH --exclusive\n"
        "#SBATCH --mem=8G\n"
        "\n" + _WORKER_LINE
    )


def test_script_user_dict_directives(tmp_path, crab_root, sbatch):
    _, script = _submit(
        tmp_path,
        crab_root,
        numnodes=2,
        ppn=4,
        extrainfo="bench",
        sbatch_directives={"account": "x", "exclusive": True, "requeue": False, "time": "01:00:00"},
    )
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_bench\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=01:00:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "#SBATCH --account=x\n"
        "#SBATCH --exclusive\n"
        "\n" + _WORKER_LINE
    )


def test_script_short_job_name_flag_does_not_replace_job_name(tmp_path, crab_root, sbatch):
    _, script = _submit(
        tmp_path,
        crab_root,
        numnodes=2,
        ppn=4,
        extrainfo="bench",
        sbatch_directives=["-J myname"],
    )
    # As it is today: "-J name" has the key "J", so it does not replace --job-name and both
    # lines are written.
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_bench\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:10:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "#SBATCH -J myname\n"
        "\n" + _WORKER_LINE
    )


def test_script_job_name_is_sanitised_and_truncated(tmp_path, crab_root, sbatch):
    _, script = _submit(tmp_path, crab_root, numnodes=2, ppn=4, extrainfo="my bench/run#1 extra")
    assert script == (
        "#!/bin/bash\n"
        "\n"
        "#SBATCH --job-name=crab_my_bench_r\n"
        "#SBATCH --output=<DATA>/slurm_output.log\n"
        "#SBATCH --error=<DATA>/slurm_error.log\n"
        "#SBATCH --time=00:10:00\n"
        "#SBATCH --nodes=2\n"
        "#SBATCH --ntasks-per-node=4\n"
        "\n" + _WORKER_LINE
    )


# --------------------------------------------------------------------------- #
# sbatch call, result, failure and interrupt
# --------------------------------------------------------------------------- #
def test_submit_result_and_sbatch_argv(tmp_path, crab_root, sbatch):
    result, _ = _submit(tmp_path, crab_root, numnodes=2, ppn=4)
    data_dir = result["data_dir"]
    assert result == {"job_id": "4242", "data_dir": data_dir, "system": "testsys"}
    assert Path(data_dir).parent == tmp_path / "data" / "testsys"
    assert sbatch.calls == [
        (["sbatch", f"{data_dir}/crab_job.sh"], {"text": True, "stderr": subprocess.STDOUT})
    ]


def test_submit_without_job_id_in_output_returns_none(tmp_path, crab_root, sbatch):
    sbatch.output = "sbatch: something unexpected"
    result, _ = _submit(tmp_path, crab_root, numnodes=2, ppn=4)
    assert result["job_id"] is None


def test_refused_sbatch_raises_with_slurms_text(tmp_path, crab_root, monkeypatch):
    refusal = subprocess.CalledProcessError(
        1,
        ["sbatch", "crab_job.sh"],
        output="sbatch: error: invalid account\nsbatch: error: Batch job submission failed\n",
    )

    def refuse(cmd: list[str], **kwargs: Any) -> str:
        raise refusal

    monkeypatch.setattr(subprocess, "check_output", refuse)
    with pytest.raises(RuntimeError) as excinfo:
        _submit(tmp_path, crab_root, numnodes=2, ppn=4)
    assert str(excinfo.value) == (
        "sbatch refused the job (exit 1): sbatch: error: invalid account\n"
        "sbatch: error: Batch job submission failed"
    )


def test_refused_sbatch_without_output_says_no_message(tmp_path, crab_root, monkeypatch):
    def refuse(cmd: list[str], **kwargs: Any) -> str:
        raise subprocess.CalledProcessError(3, cmd, output=None)

    monkeypatch.setattr(subprocess, "check_output", refuse)
    with pytest.raises(RuntimeError) as excinfo:
        _submit(tmp_path, crab_root, numnodes=2, ppn=4)
    assert str(excinfo.value) == "sbatch refused the job (exit 3): no message"


def test_interrupt_after_job_id_runs_scancel(tmp_path, crab_root, sbatch, monkeypatch):
    run_calls: list[tuple[list[str], dict[str, Any]]] = []

    def record_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        run_calls.append((list(cmd), kwargs))
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", record_run)

    def interrupt_on_job_id(message: str) -> None:
        if "Submitted batch job" in str(message):
            raise KeyboardInterrupt

    engine = _make_engine()
    engine.log.info.side_effect = interrupt_on_job_id
    with pytest.raises(KeyboardInterrupt):
        engine.run(_config(tmp_path, numnodes=2, ppn=4), {}, is_worker=False)
    assert run_calls == [(["scancel", "4242"], {"check": False})]


# --------------------------------------------------------------------------- #
# srun / mpirun launch strings
# --------------------------------------------------------------------------- #
@pytest.fixture
def launch_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _LAUNCH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _slurm_line(nodes: list[str], ppn: int, cmd: str, launcher: str | None = None) -> str:
    return launch_line(
        launcher_for("slurm", launcher, os.environ), Placement(tuple(nodes), ppn), cmd
    )


def test_srun_default_one_node(launch_env):
    line = _slurm_line(["n1"], 1, "./app")
    assert line == "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_srun_three_nodes_with_pinning_flags(launch_env):
    launch_env.setenv("CRAB_PINNING_FLAGS", "--cpu-bind=cores")
    line = _slurm_line(["n1", "n2", "n3"], 4, "./app -x 1")
    assert line == "srun --export=ALL --nodelist n1,n2,n3 --cpu-bind=cores -n 12 -N 3 ./app -x 1"


def test_srun_launcher_argument_with_options(launch_env):
    line = _slurm_line(["n1"], 1, "./app", launcher="srun --mpi=pmix")
    assert line == "srun --mpi=pmix --export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_mpirun_from_env_with_flags(launch_env):
    launch_env.setenv("CRAB_MPIRUN", "mpirun")
    launch_env.setenv("CRAB_MPIRUN_ADDITIONAL_FLAGS", "--bind-to core")
    launch_env.setenv("CRAB_MPIRUN_MAP_BY_NODE_FLAG", "--map-by node")
    launch_env.setenv("CRAB_PINNING_FLAGS", "--cpu-bind=cores")
    line = _slurm_line(["n1", "n2"], 4, "./app")
    # The node list and the pinning flags are not used by mpirun.
    assert line == "mpirun --bind-to core --map-by node -np 8 ./app"


def test_mpirun_launcher_argument_overrides_env(launch_env):
    launch_env.setenv("CRAB_MPIRUN", "srun")
    launch_env.setenv("CRAB_MPIRUN_ADDITIONAL_FLAGS", "--bind-to core")
    line = _slurm_line(["n1", "n2"], 2, "./app", launcher="/opt/ompi/bin/mpirun")
    assert line == "/opt/ompi/bin/mpirun --bind-to core -np 4 ./app"


def test_launch_string_collapses_whitespace_inside_quotes(launch_env):
    cmd = """./app --msg "hello   world"  'a    b'"""
    line = _slurm_line(["n1"], 1, cmd)
    # As it is today: runs of spaces inside quoted arguments are collapsed too.
    assert line == """srun --export=ALL --nodelist n1 -n 1 -N 1 ./app --msg "hello world" 'a b'"""


class _FakePopen:
    """Stands in for `subprocess.Popen`: starts nothing, writes nothing."""

    instances: list[_FakePopen] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.pid = 4242
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""
        _FakePopen.instances.append(self)


class _WiringJob:
    def __init__(self, run_dir: Path, command: str, node_list: list[str]) -> None:
        self.id_num = 0
        self.node_list = node_list
        self.run_dir = str(run_dir)
        self._command = command

    def run_app(self) -> str:
        return self._command

    def set_process(self, process: Any) -> None:
        self.process = process


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> type[_FakePopen]:
    _FakePopen.instances = []
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    return _FakePopen


def _script_through_run_job(
    tmp_path: Path,
    launch_mode_arg: Any,
    nodes: list[str],
    ppn: int,
    cmd: str,
    **kwargs: Any,
) -> str:
    job = _WiringJob(tmp_path, cmd, nodes)
    process_manager.run_job(job, launch_mode_arg, ppn, MagicMock(), **kwargs)
    return (tmp_path / ".wrappers" / "app_0.sh").read_text()


def test_run_job_writes_the_srun_script_through_the_launcher(launch_env, fake_popen, tmp_path):
    launch_env.setenv("CRAB_PINNING_FLAGS", "--cpu-bind=cores")
    script = _script_through_run_job(
        tmp_path,
        "slurm",
        ["n1", "n2"],
        2,
        "./app -x 1",
        pre_commands=["module load x"],
    )
    assert script == (
        "#!/bin/bash\n"
        "if [ -f /etc/profile.d/modules.sh ]; then\n"
        "    source /etc/profile.d/modules.sh\n"
        "fi\n"
        "\n"
        "module load x\n"
        "\n"
        "# Execute workload\n"
        "srun --export=ALL --nodelist n1,n2 --cpu-bind=cores -n 4 -N 2 ./app -x 1\n"
    )
    assert [p.argv for p in fake_popen.instances] == [
        ["bash", str(tmp_path / ".wrappers" / "app_0.sh")]
    ]


def test_run_job_uses_the_launcher_override_and_mpirun_flags(launch_env, fake_popen, tmp_path):
    launch_env.setenv("CRAB_MPIRUN_ADDITIONAL_FLAGS", "--bind-to core")
    script = _script_through_run_job(
        tmp_path,
        "slurm",
        ["n1", "n2"],
        2,
        "./app -x 1",
        launcher="/opt/ompi/bin/mpirun",
    )
    assert script.splitlines()[-1] == "/opt/ompi/bin/mpirun --bind-to core -np 4 ./app -x 1"


def test_run_job_local_mode_writes_the_command_as_given(launch_env, fake_popen, tmp_path):
    cmd = '  ./app --msg "a   b"  '
    script = _script_through_run_job(tmp_path, "local", ["n1"], 1, cmd)
    assert script.splitlines()[-1] == cmd


def test_run_job_in_an_unported_mode_raises_before_starting_anything(
    launch_env, fake_popen, tmp_path
):
    with pytest.raises(NotImplementedError, match="has not been ported"):
        _script_through_run_job(tmp_path, "mpi", ["n1"], 1, "./app")
    assert fake_popen.instances == []


# --------------------------------------------------------------------------- #
# status: squeue, then sacct
# --------------------------------------------------------------------------- #
class _FakeRunner:
    """Command runner that answers by program name and records every argv."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> str:
        self.calls.append(list(cmd))
        answer = self.answers[cmd[0]]
        if isinstance(answer, BaseException):
            raise answer
        return answer


_SQUEUE_ARGV = ["squeue", "-h", "-o", "%i|%T", "-j"]
_SACCT_TAIL = ["-n", "-P", "-o", "JobID,State,ExitCode"]


def test_status_without_squeue_sends_every_id_to_sacct(tmp_path):
    runner = _FakeRunner(
        {
            "squeue": FileNotFoundError("squeue"),
            "sacct": "10|COMPLETED|0:0\n10.batch|COMPLETED|0:0\n",
        }
    )
    data = contract.gather_status(["10", "11"], runner=runner, crab_root=tmp_path)
    completed = {"job_id": "10", "state": "COMPLETED", "exit_code": "0:0", "source": "sacct"}
    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "jobs": [completed, {"job_id": "11", "state": "UNKNOWN", "source": "none"}],
    }
    assert runner.calls == [
        [*_SQUEUE_ARGV, "10,11"],
        ["sacct", "-j", "10", *_SACCT_TAIL],
        ["sacct", "-j", "11", *_SACCT_TAIL],
    ]


def test_status_squeue_blank_line_and_empty_states(tmp_path):
    runner = _FakeRunner({"squeue": "\n20|\n30\n", "sacct": ""})
    data = contract.gather_status(["20", "30", "40"], runner=runner, crab_root=tmp_path)
    assert data["jobs"] == [
        {"job_id": "20", "state": "UNKNOWN", "source": "squeue"},
        {"job_id": "30", "state": "UNKNOWN", "source": "squeue"},
        {"job_id": "40", "state": "UNKNOWN", "source": "none"},
    ]
    assert runner.calls == [
        [*_SQUEUE_ARGV, "20,30,40"],
        ["sacct", "-j", "40", *_SACCT_TAIL],
    ]


@pytest.mark.parametrize(
    ("sacct_out", "expected"),
    [
        (
            "50|CANCELLED by 12345|0:0\n50.batch|CANCELLED|0:15\n",
            # As it is today: the "by <uid>" suffix is passed through unchanged.
            {"job_id": "50", "state": "CANCELLED by 12345", "exit_code": "0:0", "source": "sacct"},
        ),
        (
            "50|TIMEOUT|0:0\n50.batch|CANCELLED|0:15\n",
            {"job_id": "50", "state": "TIMEOUT", "exit_code": "0:0", "source": "sacct"},
        ),
        (
            "50|FAILED|1:0\n50.batch|FAILED|1:0\n",
            {"job_id": "50", "state": "FAILED", "exit_code": "1:0", "source": "sacct"},
        ),
        (
            "50.batch|COMPLETED|0:0\n50.extern|COMPLETED|0:0\n",
            {"job_id": "50", "state": "UNKNOWN", "source": "none"},
        ),
        ("", {"job_id": "50", "state": "UNKNOWN", "source": "none"}),
    ],
    ids=["cancelled-by", "timeout", "failed", "no-primary-row", "empty"],
)
def test_status_sacct_shapes(tmp_path, sacct_out, expected):
    runner = _FakeRunner({"squeue": "", "sacct": sacct_out})
    data = contract.gather_status(["50"], runner=runner, crab_root=tmp_path)
    assert data == {"schema": contract.CONTRACT_SCHEMA, "jobs": [expected]}
    assert runner.calls == [[*_SQUEUE_ARGV, "50"], ["sacct", "-j", "50", *_SACCT_TAIL]]


# --------------------------------------------------------------------------- #
# cancel
# --------------------------------------------------------------------------- #
def test_cancel_scancel_failure_reports_exit_code(tmp_path):
    runner = _FakeRunner({"scancel": subprocess.CalledProcessError(1, ["scancel", "77"])})
    data = contract.gather_cancel("77", runner=runner, crab_root=tmp_path)
    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "job_id": "77",
        "cancelled": False,
        "detail": "scancel exited 1; the job may already be gone.",
    }
    assert runner.calls == [["scancel", "77"]]


def test_cancel_without_scancel_binary_exact_detail(tmp_path):
    runner = _FakeRunner({"scancel": FileNotFoundError("scancel")})
    data = contract.gather_cancel("77", runner=runner, crab_root=tmp_path)
    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "job_id": "77",
        "cancelled": False,
        "detail": "scancel is not available on this host.",
    }
