"""Tests for the local scheduler's job state: the XDG location, the `local-<n>` counter, records.

Pure logic plus real file locks in `tmp_path`. Nothing here signals a process.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from crab.core.execution.scheduler import local_state
from crab.core.execution.scheduler.local_state import (
    JOB_STATES,
    JobRecord,
    acquire_hosts,
    allocate_id,
    check_host,
    is_alive,
    jobs_dir,
    open_liveness_lock,
    read_job_state,
    read_record,
    release_hosts,
    route,
    state_dir,
    write_job_state,
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


# --------------------------------------------------------------------------- #
# liveness lock
# --------------------------------------------------------------------------- #
def test_open_liveness_lock_creates_the_file_and_holds_it(tmp_path: Path) -> None:
    jobs = tmp_path / "state" / "jobs"

    fd = open_liveness_lock(jobs, "local-1")
    try:
        path = jobs / "local-1.lock"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        other = os.open(path, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
        finally:
            os.close(other)
    finally:
        os.close(fd)


def test_open_liveness_lock_twice_raises_blocking_io_error(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    fd = open_liveness_lock(jobs, "local-1")
    try:
        with pytest.raises(BlockingIOError):
            open_liveness_lock(jobs, "local-1")
    finally:
        os.close(fd)

    os.close(open_liveness_lock(jobs, "local-1"))  # free again once the holder is gone


def test_open_liveness_lock_refuses_a_non_local_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid job id"):
        open_liveness_lock(tmp_path / "jobs", "../local-1")
    assert not (tmp_path / "jobs").exists()


def test_is_alive_follows_the_held_lock(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    fd = open_liveness_lock(jobs, "local-1")

    assert is_alive(jobs, "local-1") is True
    assert is_alive(jobs, "local-1") is True  # the probe did not take the lock over

    os.close(fd)

    assert is_alive(jobs, "local-1") is False
    os.close(open_liveness_lock(jobs, "local-1"))  # the probe left nothing held


def test_is_alive_without_a_lock_file_is_false_and_creates_nothing(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    assert is_alive(jobs, "local-1") is False  # no jobs dir at all

    jobs.mkdir()
    assert is_alive(jobs, "local-1") is False
    assert list(jobs.iterdir()) == []


def test_is_alive_refuses_a_non_local_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid job id"):
        is_alive(tmp_path / "jobs", "123")


# --------------------------------------------------------------------------- #
# host locks
# --------------------------------------------------------------------------- #
def test_host_locks_exclude_each_other_until_released(tmp_path: Path) -> None:
    held = acquire_hosts(tmp_path, ["b", "a"])
    assert len(held) == 2
    try:
        with pytest.raises(BlockingIOError):
            acquire_hosts(tmp_path, ["a"], blocking=False)
        free = acquire_hosts(tmp_path, ["c"], blocking=False)
        release_hosts(free)
    finally:
        release_hosts(held)

    again = acquire_hosts(tmp_path, ["a", "b"], blocking=False)
    release_hosts(again)
    assert sorted(p.name for p in (tmp_path / "hosts").iterdir()) == ["a.lock", "b.lock", "c.lock"]


def test_a_failed_non_blocking_acquire_keeps_nothing(tmp_path: Path) -> None:
    held = acquire_hosts(tmp_path, ["b"])
    try:
        with pytest.raises(BlockingIOError):
            acquire_hosts(tmp_path, ["a", "b", "c"], blocking=False)  # takes a, fails on b
        release_hosts(acquire_hosts(tmp_path, ["a", "c"], blocking=False))
    finally:
        release_hosts(held)


def test_acquire_hosts_ignores_duplicate_names(tmp_path: Path) -> None:
    held = acquire_hosts(tmp_path, ["a", "a"], blocking=False)
    try:
        assert len(held) == 1
    finally:
        release_hosts(held)


def test_acquire_hosts_takes_names_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    real_open = local_state.os.open

    def spy(path: Any, flags: int, mode: int = 0o777) -> int:
        opened.append(Path(path).name)
        return real_open(path, flags, mode)

    monkeypatch.setattr(local_state.os, "open", spy)

    release_hosts(acquire_hosts(tmp_path, ["c", "a", "b"]))

    assert opened == ["a.lock", "b.lock", "c.lock"]


@pytest.mark.parametrize("name", ["", "a/b", "../x", ".hidden", "/abs"])
def test_acquire_hosts_refuses_names_that_could_escape(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="host name"):
        acquire_hosts(tmp_path, ["fine", name])

    assert not (tmp_path / "hosts" / "fine.lock").exists()  # nothing was taken first


# --------------------------------------------------------------------------- #
# job state file
# --------------------------------------------------------------------------- #
def test_job_states_are_the_written_ones() -> None:
    assert JOB_STATES == ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT")


def test_job_state_round_trip_and_moves_forward(tmp_path: Path) -> None:
    assert read_job_state(tmp_path) is None

    write_job_state(tmp_path, "PENDING")
    assert read_job_state(tmp_path) == "PENDING"

    write_job_state(tmp_path, "RUNNING")
    assert read_job_state(tmp_path) == "RUNNING"

    body = json.loads((tmp_path / "local_state.json").read_text())
    assert body["state"] == "RUNNING"
    assert isinstance(body["at"], str) and body["at"]
    assert [p.name for p in tmp_path.iterdir()] == ["local_state.json"]


def test_write_job_state_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_job_state(tmp_path, "PENDING")
    seen: list[str | None] = []
    real_replace = local_state.os.replace

    def spy(src: Any, dst: Any) -> None:
        seen.append(read_job_state(tmp_path))  # the old file is intact until the replace
        assert Path(src).name.endswith(".tmp")
        real_replace(src, dst)

    monkeypatch.setattr(local_state.os, "replace", spy)

    write_job_state(tmp_path, "COMPLETED")

    assert seen == ["PENDING"]
    assert read_job_state(tmp_path) == "COMPLETED"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("state", ["LOST", "running", "", "DONE"])
def test_write_job_state_refuses_a_state_that_is_not_written(tmp_path: Path, state: str) -> None:
    with pytest.raises(ValueError, match="job state"):
        write_job_state(tmp_path, state)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1]", "{}", '{"state": 5}', '{"state": "LOST"}', '{"state": "nope"}'],
)
def test_read_job_state_malformed_names_the_file(tmp_path: Path, text: str) -> None:
    (tmp_path / "local_state.json").write_text(text)

    with pytest.raises(ValueError, match=r"local_state\.json"):
        read_job_state(tmp_path)
