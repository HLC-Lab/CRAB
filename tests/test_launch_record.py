"""`run_job` records how each app was launched in the job's `crab_run.json` (first launch only)
and, under mpirun, tees the launcher's stderr into a bindings log in the experiment directory."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crab.core.execution.launcher import dialect
from crab.core.execution.settings import LauncherSpec
from crab.core.process import manager as process_manager
from crab.core.provenance import record_launch

SRUN = LauncherSpec("srun", ("srun",), "", (), ())
DIRECT = LauncherSpec("direct", (), "", (), ())


def _mpirun(command: str = "/opt/ompi/bin/mpirun") -> LauncherSpec:
    return LauncherSpec("mpirun", (command,), "openmpi5", (), ())


class _FakePopen:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.pid = 4242
        self.returncode = None
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""


class _Job:
    """An app as the runner leaves it: `run_dir`, `exp_dir` and `run_number` are set per run."""

    def __init__(
        self, exp_dir: Path, run_number: int | None = 1, app_id: int = 0, ppn: int = 2
    ) -> None:
        self.id_num = app_id
        self.ppn = ppn
        self.node_list = ["n1"]
        self.exp_dir = str(exp_dir)
        self.run_dir = str(exp_dir / f"run_{run_number}" / f"app_{app_id}")
        os.makedirs(self.run_dir, exist_ok=True)
        if run_number is not None:
            self.run_number = run_number

    def run_app(self) -> str:
        return "./app -x 1"

    def set_process(self, process: Any) -> None:
        self.process = process


@pytest.fixture(autouse=True)
def _forget_registered_processes() -> Iterator[None]:
    """run_job registers every process it starts; drop them so later tests see a clean registry."""
    before = set(process_manager._registry)
    yield
    for pid in set(process_manager._registry) - before:
        process_manager._registry.pop(pid, None)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A job directory holding `crab_run.json` and the experiment directory `<job>/e1`."""
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    job_dir = tmp_path / "job"
    exp_dir = job_dir / "e1"
    exp_dir.mkdir(parents=True)
    (job_dir / "crab_run.json").write_text(json.dumps({"results_schema": 1}))
    return job_dir, exp_dir


def _launches(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "crab_run.json").read_text())["launches"]


def _script(job: _Job) -> str:
    return (Path(job.run_dir) / ".wrappers" / f"app_{job.id_num}.sh").read_text()


def _run(job: _Job, spec: LauncherSpec) -> None:
    with patch.dict(os.environ, {"PATH": "/p"}, clear=True):
        process_manager.run_job(job, spec, MagicMock())


def test_an_mpirun_launch_is_recorded_with_its_line_kind_dialect_and_binding_report(
    layout: tuple[Path, Path],
) -> None:
    job_dir, exp_dir = layout
    job = _Job(exp_dir)
    _run(job, _mpirun())

    bindings = str(exp_dir / "bindings_app_0.log")
    line = "/opt/ompi/bin/mpirun -n 2 --host n1:2 --display bindings ./app -x 1"
    assert _launches(job_dir) == {
        "e1": {
            "0": {"line": line, "kind": "mpirun", "dialect": "openmpi5", "binding_report": bindings}
        }
    }
    lines = _script(job).splitlines()
    assert lines[-2] == f'echo "# run 1" >> {shlex.quote(bindings)}'
    assert lines[-1] == f"{line} 2> >(tee -a {shlex.quote(bindings)} >&2)"


def test_the_bindings_path_is_shell_quoted(layout: tuple[Path, Path]) -> None:
    job_dir, exp_dir = layout
    odd = exp_dir.parent / "my exp"
    odd.mkdir()
    job = _Job(odd)
    _run(job, _mpirun())
    assert shlex.quote(str(odd / "bindings_app_0.log")) in _script(job)
    assert "'" in shlex.quote(str(odd / "bindings_app_0.log"))


def test_without_a_run_number_the_header_is_skipped(layout: tuple[Path, Path]) -> None:
    _, exp_dir = layout
    job = _Job(exp_dir, run_number=None)
    _run(job, _mpirun())
    script = _script(job)
    assert "# run" not in script
    assert "tee -a" in script


@pytest.mark.parametrize("spec", [SRUN, DIRECT], ids=["srun", "direct"])
def test_srun_and_direct_record_no_binding_report_and_no_tee(
    layout: tuple[Path, Path], spec: LauncherSpec
) -> None:
    job_dir, exp_dir = layout
    job = _Job(exp_dir)
    _run(job, spec)

    record = _launches(job_dir)["e1"]["0"]
    assert record["kind"] == spec.kind
    assert record["dialect"] is None
    assert record["binding_report"] is None
    assert "tee" not in _script(job)
    assert not (exp_dir / "bindings_app_0.log").exists()
    assert record["line"] == _script(job).splitlines()[-1]


def test_a_second_launch_of_the_same_app_does_not_rewrite_the_record(
    layout: tuple[Path, Path],
) -> None:
    job_dir, exp_dir = layout
    _run(_Job(exp_dir, run_number=1), _mpirun())
    first = _launches(job_dir)
    _run(_Job(exp_dir, run_number=2), _mpirun("/other/mpirun"))
    assert _launches(job_dir) == first
    assert "# run 2" in _script(_Job(exp_dir, run_number=2))


def test_another_app_adds_its_own_entry(layout: tuple[Path, Path]) -> None:
    job_dir, exp_dir = layout
    _run(_Job(exp_dir, app_id=0), DIRECT)
    _run(_Job(exp_dir, app_id=1), DIRECT)
    assert set(_launches(job_dir)["e1"]) == {"0", "1"}


def test_a_missing_or_unreadable_crab_run_json_is_skipped(layout: tuple[Path, Path]) -> None:
    job_dir, exp_dir = layout
    (job_dir / "crab_run.json").unlink()
    _run(_Job(exp_dir), DIRECT)
    assert not (job_dir / "crab_run.json").exists()

    (job_dir / "crab_run.json").write_text("{not json")
    _run(_Job(exp_dir), DIRECT)
    assert (job_dir / "crab_run.json").read_text() == "{not json"
    record_launch(str(job_dir / "absent"), "e1", "0", {"line": "x"})  # no raise


def test_a_job_without_an_experiment_directory_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    job = _Job(tmp_path / "x")
    del job.exp_dir
    _run(job, _mpirun())
    assert "tee" not in _script(job)


def test_the_tee_keeps_stderr_on_the_app_pipe_and_fills_the_bindings_log(
    tmp_path: Path,
) -> None:
    """A real bash run: a fake mpirun that writes to stderr and stdout."""
    job_dir = tmp_path / "job"
    exp_dir = job_dir / "e1"
    exp_dir.mkdir(parents=True)
    (job_dir / "crab_run.json").write_text("{}")
    fake = tmp_path / "fake_mpirun"
    fake.write_text("#!/bin/sh\necho bind >&2\necho out\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    spec = LauncherSpec("mpirun", (str(fake),), "openmpi5", (), ())

    for run_number in (1, 2):
        job = _Job(exp_dir, run_number=run_number, ppn=1)
        with patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True):
            process_manager.run_job(job, spec, MagicMock())
        assert job.process.wait(timeout=20) == 0
        job._stream_thread.join(timeout=5)
        assert b"".join(job.raw_stdout_buffer) == b"out\n"
        assert job.process.stderr.read() == b"bind\n"
        job.process.stderr.close()

    # tee may finish just after bash exits; wait for it through the file content
    log = exp_dir / "bindings_app_0.log"
    for _ in range(100):
        if log.exists() and log.read_text().count("bind") == 2:
            break
        subprocess.run(["sleep", "0.05"], check=True)
    assert log.read_text() == "# run 1\nbind\n# run 2\nbind\n"


def test_the_dialect_is_recorded_as_resolved_when_the_spec_says_auto(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, exp_dir = layout
    monkeypatch.setattr(dialect, "probe", lambda command: "hydra")
    _run(_Job(exp_dir), LauncherSpec("mpirun", ("mpirun",), "auto", (), ()))
    assert _launches(job_dir)["e1"]["0"]["dialect"] == "hydra"


def test_launches_are_keyed_by_the_config_key_of_the_app(tmp_path: Path) -> None:
    """Config keys "3" and "7" become app ids 0 and 1; crab_run.json uses the config keys."""
    from crab.core.experiment.runner import ExperimentRunner
    from crab.core.provenance import write_provenance
    from settings_fixtures import LOCAL_DIRECT

    wrapper = tmp_path / "w.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        "        return 'true'\n\n"
        "    def read_data(self):\n"
        "        return [[1.0]]\n"
    )
    config = {
        "experiments": {
            "pair": {
                "apps": {"3": {"path": str(wrapper)}, "7": {"path": str(wrapper)}},
                "local_options": {"minruns": "1", "maxruns": "1"},
            }
        }
    }
    out = tmp_path / "out"
    out.mkdir()
    write_provenance(str(out), config, ["n1", "n2"])
    runner = ExperimentRunner(
        exp_name="pair",
        config=config["experiments"]["pair"],
        global_options={"numnodes": "2"},
        node_list=["n1", "n2"],
        output_dir=str(out),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(out))

    data = json.loads((out / "crab_run.json").read_text())
    assert set(data["launches"]["pair"]) == {"3", "7"}
    assert set(data["apps"]["pair"]) == {"3", "7"}
