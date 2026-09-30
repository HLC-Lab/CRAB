"""Every job directory gets `crab_run.json`, saying which CRAB and which wrapper files produced
its results, so a CSV can always be traced to (and re-parsed with) the parser that made it."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import crab
from crab.core.provenance import RESULTS_SCHEMA, write_provenance


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
