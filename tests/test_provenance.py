"""Every job directory gets `crab_run.json`, saying which CRAB and which wrapper files produced
its results, so a CSV can always be traced to (and re-parsed with) the parser that made it."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import crab
import crab.core.wrapper_paths as wrapper_paths
from crab import paths
from crab.core.execution.launcher import dialect
from crab.core.execution.settings import from_preset
from crab.core.provenance import RESULTS_SCHEMA, record_allocation, write_provenance
from settings_fixtures import slurm_settings


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def wrappers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "wrappers"
    (repo / "blink").mkdir(parents=True)
    (repo / "blink" / "a2a.py").write_text("class app: pass\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(repo))
    return repo


def _config() -> dict:
    return {"experiments": {"e1": {"apps": {"0": {"path": "blink/a2a.py"}}}}}


def test_the_sidecar_records_versions_wrapper_commit_and_hashes(
    tmp_path: Path, wrappers: Path
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    write_provenance(str(job), _config(), ["n1", "n2"])
    data = json.loads((job / "crab_run.json").read_text())

    assert data["results_schema"] == RESULTS_SCHEMA == 1
    assert data["crab_version"] == crab.__version__
    assert data["wrapper_api"] == 1
    assert data["nodes"] == ["n1", "n2"]
    assert data["wrappers"] == {
        "dir": str(wrappers),
        "commit": _git(wrappers, "rev-parse", "HEAD"),
        "dirty": False,
    }
    wrapper_file = wrappers / "blink" / "a2a.py"
    assert data["apps"]["e1"]["0"] == {
        "path": str(wrapper_file),
        "sha256": hashlib.sha256(wrapper_file.read_bytes()).hexdigest(),
    }


def test_uncommitted_wrapper_edits_are_flagged(tmp_path: Path, wrappers: Path) -> None:
    (wrappers / "blink" / "a2a.py").write_text("class app: pass  # edited\n")
    write_provenance(str(tmp_path), _config(), [])
    assert json.loads((tmp_path / "crab_run.json").read_text())["wrappers"]["dirty"] is True


def test_no_git_and_missing_files_are_recorded_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(plain))
    write_provenance(str(tmp_path), _config(), [])
    data = json.loads((tmp_path / "crab_run.json").read_text())
    assert data["wrappers"]["commit"] is None and data["wrappers"]["dirty"] is None
    assert data["apps"]["e1"]["0"]["sha256"] is None


# --------------------------------------------------------------------------- #
# crab_commit: only a checkout has one
# --------------------------------------------------------------------------- #
def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "f").write_text("x")
    _git(path, "init", "-q")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return path


def test_a_checkout_records_the_checkouts_commit(
    tmp_path: Path, wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "checkout")
    monkeypatch.setattr(paths, "checkout_root", lambda: repo)
    write_provenance(str(tmp_path), _config(), [])
    data = json.loads((tmp_path / "crab_run.json").read_text())
    assert data["crab_commit"] == _git(repo, "rev-parse", "HEAD")


def test_installed_has_no_crab_commit_even_inside_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _git_repo(tmp_path / "crab-home")
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    monkeypatch.setattr(wrapper_paths, "_CRAB_ROOT", str(home))
    write_provenance(str(tmp_path), _config(), [])
    data = json.loads((tmp_path / "crab_run.json").read_text())
    assert data["crab_commit"] is None
    assert data["crab_version"] == crab.__version__
    assert data["wrappers"]["dir"] == str(home / "wrappers")


# --------------------------------------------------------------------------- #
# The `execution` block
# --------------------------------------------------------------------------- #
def _local_mpirun(dialect: str = "auto"):
    return from_preset(
        {
            "scheduler": "local",
            "hosts": ["ws1:4", "ws2:4"],
            "launcher": "mpirun",
            "launchers": {"mpirun": {"command": "/opt/ompi/bin/mpirun", "dialect": dialect}},
        },
        "lab",
    )


def test_local_mpirun_execution_records_the_probed_dialect_and_version(
    tmp_path: Path, wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[str] = []

    def fake_probe(command: str) -> str:
        probed.append(command)
        return "openmpi5"

    monkeypatch.setattr(dialect, "probe", fake_probe)
    monkeypatch.setattr(
        dialect, "probe_output", lambda command: "mpirun (Open MPI) 5.0.11\n\nReport bugs\n"
    )
    write_provenance(str(tmp_path), _config(), ["ws1", "ws2"], _local_mpirun())
    data = json.loads((tmp_path / "crab_run.json").read_text())

    assert probed == ["/opt/ompi/bin/mpirun"]
    assert data["execution"] == {
        "scheduler": "local",
        "hosts_used": ["ws1", "ws2"],
        "launcher": "mpirun",
        "mpirun": {
            "command": "/opt/ompi/bin/mpirun",
            "dialect": "openmpi5",
            "version": "mpirun (Open MPI) 5.0.11",
        },
    }


def test_a_configured_dialect_is_recorded_without_probing(
    tmp_path: Path, wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(command: str) -> str:
        raise AssertionError("must not probe")

    monkeypatch.setattr(dialect, "probe", refuse)
    write_provenance(str(tmp_path), _config(), ["ws1"], _local_mpirun("hydra"))
    mpirun = json.loads((tmp_path / "crab_run.json").read_text())["execution"]["mpirun"]
    assert mpirun == {"command": "/opt/ompi/bin/mpirun", "dialect": "hydra", "version": None}


def test_slurm_srun_execution_does_not_probe(
    tmp_path: Path, wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(command: str) -> str:
        raise AssertionError("must not probe")

    monkeypatch.setattr(dialect, "probe", refuse)
    write_provenance(str(tmp_path), _config(), ["n1", "n2"], slurm_settings())
    execution = json.loads((tmp_path / "crab_run.json").read_text())["execution"]
    assert execution["scheduler"] == "slurm"
    assert execution["hosts_used"] == ["n1", "n2"]
    assert execution["launcher"] == "srun"
    assert execution["mpirun"] == {"command": "mpirun", "dialect": "auto", "version": None}


def test_a_failed_probe_records_the_message_and_still_writes_the_file(
    tmp_path: Path, wrappers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(command: str) -> str:
        raise ValueError("Cannot find 'mpirun' to detect its MPI.")

    monkeypatch.setattr(dialect, "probe", failing)
    write_provenance(str(tmp_path), _config(), ["ws1"], _local_mpirun())
    data = json.loads((tmp_path / "crab_run.json").read_text())
    assert data["execution"]["mpirun"] == {
        "command": "/opt/ompi/bin/mpirun",
        "dialect": None,
        "version": None,
        "probe_error": "Cannot find 'mpirun' to detect its MPI.",
    }
    assert data["nodes"] == ["ws1"]


def test_without_settings_there_is_no_execution_block(tmp_path: Path, wrappers: Path) -> None:
    write_provenance(str(tmp_path), _config(), ["n1"])
    assert "execution" not in json.loads((tmp_path / "crab_run.json").read_text())


def test_record_allocation_adds_the_entry_and_keeps_the_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "crab_run.json"
    path.write_text(json.dumps({"nodes": ["n0", "n1"], "launches": {"e": {"0": {"line": "x"}}}}))
    record = {"mode": "random", "seed": 7, "apps": {"0": ["n0"], "1": ["n1"]}}

    record_allocation(str(tmp_path), "e", record)

    data = json.loads(path.read_text())
    assert data["allocations"] == {"e": record}
    assert data["nodes"] == ["n0", "n1"]
    assert data["launches"] == {"e": {"0": {"line": "x"}}}


def test_record_allocation_keeps_one_entry_per_experiment(tmp_path: Path) -> None:
    path = tmp_path / "crab_run.json"
    path.write_text("{}")

    record_allocation(str(tmp_path), "a", {"mode": "linear", "apps": {"0": ["n0"]}})
    record_allocation(str(tmp_path), "b", {"mode": "linear", "apps": {"0": ["n1"]}})
    record_allocation(str(tmp_path), "b", {"mode": "linear", "apps": {"0": ["n2"]}})

    allocations = json.loads(path.read_text())["allocations"]
    assert sorted(allocations) == ["a", "b"]
    assert allocations["b"]["apps"] == {"0": ["n2"]}


def test_record_allocation_skips_a_missing_or_corrupt_file(tmp_path: Path) -> None:
    record_allocation(str(tmp_path), "e", {"mode": "linear", "apps": {}})
    assert not (tmp_path / "crab_run.json").exists()

    (tmp_path / "crab_run.json").write_text("{not json")
    record_allocation(str(tmp_path), "e", {"mode": "linear", "apps": {}})
    assert (tmp_path / "crab_run.json").read_text() == "{not json"
