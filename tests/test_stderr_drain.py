"""An app that writes a lot to stderr must not stall CRAB: the stderr pipe is drained while the
app runs, not read only after it exits (process/manager.py). A pipe holds about 64 KiB; past
that the app blocks on its write, so `wait_timed` ran to its timeout and `end_job` returned
only the first 64 KiB."""

from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.execution.settings import LauncherSpec
from crab.core.process import manager as process_manager
from crab.core.process.manager import end_job, run_job, wait_timed

DIRECT = LauncherSpec("direct", (), "", (), ())
KIB = 1024


class _Job:
    def __init__(self, run_dir: Path, command: str) -> None:
        self.id_num = 0
        self.ppn = 1
        self.node_list = ["localhost"]
        self.run_dir = str(run_dir)
        self._command = command
        self.output: tuple[bytes, bytes] | None = None

    def run_app(self) -> str:
        return self._command

    def set_process(self, process) -> None:
        self.process = process

    def set_output(self, out: bytes, err: bytes) -> None:
        self.output = (out, err)


def _python(code: str) -> str:
    return shlex.join([sys.executable, "-c", code])


def _job(tmp_path: Path, code: str) -> _Job:
    run_dir = tmp_path / "run_1" / "app_0"
    run_dir.mkdir(parents=True)
    return _Job(run_dir, _python(code))


def test_an_app_that_writes_a_megabyte_to_stderr_finishes_and_keeps_all_of_it(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path, f"import sys; sys.stderr.write('e' * {1024 * KIB}); print('ok')")
    run_job(job, DIRECT, MagicMock())

    started = time.monotonic()
    timed_out = wait_timed(job, timeout_sec=10.0, logger=MagicMock())

    assert not timed_out
    assert time.monotonic() - started < 5.0
    assert job.output is not None
    out, err = job.output
    assert out == b"ok\n"
    assert len(err) == 1024 * KIB


def test_only_the_last_of_a_huge_stderr_is_kept_and_the_cut_is_logged(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(process_manager, "STDERR_KEEP_BYTES", 100 * KIB)
    job = _job(
        tmp_path,
        f"import sys; sys.stderr.write('a' * {500 * KIB} + 'b' * {50 * KIB}); print('ok')",
    )
    logger = MagicMock()
    run_job(job, DIRECT, logger)

    assert not wait_timed(job, timeout_sec=10.0, logger=logger)

    assert job.output is not None
    assert job.output[1] == b"a" * (50 * KIB) + b"b" * (50 * KIB)
    assert any("stderr" in str(call) for call in logger.warning.call_args_list)


def test_a_stopped_app_keeps_the_stderr_it_wrote_before_the_stop(tmp_path: Path) -> None:
    job = _job(
        tmp_path,
        f"import sys, time; sys.stderr.write('e' * {200 * KIB}); sys.stderr.flush(); "
        "print('written', flush=True); time.sleep(60)",
    )
    run_job(job, DIRECT, MagicMock())

    deadline = time.monotonic() + 10.0
    while b"written\n" not in b"".join(job.raw_stdout_buffer) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert b"written\n" in b"".join(job.raw_stdout_buffer), "the app stalled on its stderr write"

    end_job(job, MagicMock())

    assert job.output is not None
    assert len(job.output[1]) == 200 * KIB
