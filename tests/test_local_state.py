"""Tests for the local scheduler's job state: the XDG location, the `local-<n>` counter, records.

Pure logic plus real file locks in `tmp_path`. Nothing here signals a process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from crab.core.execution.scheduler import local_state
from crab.core.execution.scheduler.local_state import (
    JobRecord,
    allocate_id,
    check_host,
    jobs_dir,
    read_record,
    route,
    state_dir,
    write_record,
)


# --------------------------------------------------------------------------- #
# state_dir
# --------------------------------------------------------------------------- #
def test_state_dir_uses_an_absolute_xdg_state_home() -> None:
    assert state_dir({"XDG_STATE_HOME": "/x"}) == Path("/x/crab")
    assert jobs_dir({"XDG_STATE_HOME": "/x"}) == Path("/x/crab/jobs")


@pytest.mark.parametrize("environ", [{}, {"XDG_STATE_HOME": ""}])
def test_state_dir_defaults_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environ: dict[str, str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert state_dir(environ) == tmp_path / "home" / ".local" / "state" / "crab"


def test_state_dir_ignores_a_relative_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert state_dir({"XDG_STATE_HOME": "relative/dir"}) == (
        tmp_path / "home" / ".local" / "state" / "crab"
    )


# --------------------------------------------------------------------------- #
# allocate_id
# --------------------------------------------------------------------------- #
def test_allocate_id_counts_up_from_one(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"

    assert allocate_id(jobs) == "local-1"
    assert allocate_id(jobs) == "local-2"


def test_allocate_id_counter_is_a_file(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    allocate_id(jobs)
    allocate_id(jobs)

    assert (jobs / "counter").read_text().strip() == "2"
    assert allocate_id(jobs) == "local-3"
    assert not list(jobs.glob("*.tmp"))


def test_allocate_id_refuses_a_corrupt_counter(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "counter").write_text("not a number")

    with pytest.raises(ValueError, match="counter.*; fix or delete this file"):
        allocate_id(jobs)


def test_allocate_id_skips_ids_whose_record_exists_after_a_counter_reset(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    for _ in range(2):
        write_record(jobs, _record(allocate_id(jobs)))
    (jobs / "counter").unlink()

    assert allocate_id(jobs) == "local-3"
    assert (jobs / "counter").read_text().strip() == "3"


def test_write_record_refuses_to_overwrite_an_existing_record(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    write_record(jobs, _record())

    with pytest.raises(ValueError, match=r"local-1\.json"):
        write_record(jobs, JobRecord("local-1", "other", "/elsewhere", 5, "t"))

    assert read_record(jobs, "local-1") == _record()
    assert [p.name for p in jobs.iterdir()] == ["local-1.json"]


def test_two_concurrent_processes_get_distinct_ids(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from crab.core.execution.scheduler.local_state import allocate_id\n"
        "for _ in range(20):\n"
        "    print(allocate_id(Path(sys.argv[1])))\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(jobs)], stdout=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    ids: list[str] = []
    for proc in procs:
        out, _ = proc.communicate(timeout=60)
        assert proc.returncode == 0
        ids.extend(out.split())

    assert len(ids) == 40
    assert len(set(ids)) == 40
    assert set(ids) == {f"local-{n}" for n in range(1, 41)}


# --------------------------------------------------------------------------- #
# write_record / read_record
# --------------------------------------------------------------------------- #
def _record(job_id: str = "local-1", pid: Any = 4321) -> JobRecord:
    return JobRecord(job_id, "node1", "/data/job", pid, "2026-10-08T10:00:00")


def test_record_round_trip(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    record = _record()

    write_record(jobs, record)

    assert read_record(jobs, "local-1") == record
    on_disk = json.loads((jobs / "local-1.json").read_text())
    assert on_disk == {
        "id": "local-1",
        "host": "node1",
        "data_dir": "/data/job",
        "supervisor_pid": 4321,
        "created": "2026-10-08T10:00:00",
    }


def test_write_record_leaves_no_tmp_file_and_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = tmp_path / "jobs"
    linked: list[tuple[str, str]] = []
    real_link = local_state.os.link

    def spy(src: Any, dst: Any) -> None:
        linked.append((str(src), str(dst)))
        assert Path(src).is_file()
        assert not Path(dst).exists()  # a reader sees the whole new file or no file
        real_link(src, dst)

    monkeypatch.setattr(local_state.os, "link", spy)

    write_record(jobs, _record())

    assert len(linked) == 1
    assert linked[0][1] == str(jobs / "local-1.json")
    assert linked[0][0].endswith(".tmp")
    assert [p.name for p in jobs.iterdir()] == ["local-1.json"]


def test_read_record_missing_is_none(tmp_path: Path) -> None:
    assert read_record(tmp_path / "jobs", "local-9") is None


def test_read_record_malformed_json_names_the_file(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "local-1.json").write_text("{not json")

    with pytest.raises(ValueError, match=r"local-1\.json"):
        read_record(jobs, "local-1")


@pytest.mark.parametrize(
    "mutation",
    [
        {"supervisor_pid": True},
        {"supervisor_pid": "4321"},
        {"supervisor_pid": 4321.0},
        {"supervisor_pid": None},
        {"host": 5},
        {"data_dir": None},
        {"data_dir": ""},
        {"data_dir": "relative/dir"},
        {"created": 1},
        {"id": "local-2"},
    ],
    ids=repr,
)
def test_read_record_wrong_shape_is_a_value_error(tmp_path: Path, mutation: dict[str, Any]) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    body = {
        "id": "local-1",
        "host": "node1",
        "data_dir": "/d",
        "supervisor_pid": 4321,
        "created": "t",
        **mutation,
    }
    (jobs / "local-1.json").write_text(json.dumps(body))

    with pytest.raises(ValueError, match=r"local-1\.json"):
        read_record(jobs, "local-1")


def test_read_record_missing_key_and_non_object_are_value_errors(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "local-1.json").write_text(json.dumps({"id": "local-1"}))
    (jobs / "local-2.json").write_text("[1, 2]")

    for job_id in ("local-1", "local-2"):
        with pytest.raises(ValueError, match=job_id):
            read_record(jobs, job_id)


def test_read_record_refuses_a_path_shaped_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid job id"):
        read_record(tmp_path / "jobs", "../local-1")


# --------------------------------------------------------------------------- #
# route / check_host
# --------------------------------------------------------------------------- #
def test_route_by_id_shape() -> None:
    assert route("local-7") == "local"
    assert route("12345") == "slurm"


@pytest.mark.parametrize(
    "job_id", ["local-", "local-x", "local-0", "local-07", "7x", "", "../1", "local-1/../2", "7\n"]
)
def test_route_refuses_any_other_shape(job_id: str) -> None:
    with pytest.raises(ValueError, match="invalid job id") as info:
        route(job_id)
    assert repr(job_id) in str(info.value)
    assert "local-<n>" in str(info.value)


def test_check_host_accepts_the_same_host_and_names_both_otherwise() -> None:
    check_host(_record(), "node1")

    with pytest.raises(ValueError, match=r"node1.*node2|node2.*node1"):
        check_host(_record(), "node2")
