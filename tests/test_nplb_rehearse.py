"""The laptop rehearsal (examples/nplb/rehearse.sh) and its fake Blink (examples/nplb/fake_blink.py).

The fake Blink is run as a subprocess and its stdout goes through the real crab-wrappers parser (the seam
of tests/test_nplb_campaign_wrappers.py: `_parse`, which loads `wrappers/blink/_microbench_common.py`
the way CRAB does). The script's functions are tested by sourcing it with `--source-only`. The rehearsal
itself (mpirun, the local scheduler, about 2 minutes) is not a pytest case: the gate's `full` run and the
person at the keyboard run `examples/nplb/rehearse.sh`. Nothing here needs mpirun, a GPU or a cluster.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import requires_wrappers_checkout
from test_nplb_campaign_wrappers import A2A, AGTR, ARDC, GPU_LINE, HEADER, METRICS, _parse

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "examples" / "nplb" / "fake_blink.py"
REHEARSE = REPO / "examples" / "nplb" / "rehearse.sh"
PROGRAMS = {
    "alltoall_comm_only": A2A,
    "allgather_comm_only": AGTR,
    "allreduce_b": ARDC,
}


def run_fake(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """fake_blink.py with the given arguments, a clean environment and no sleep unless asked."""
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("NPLB_FAKE", "OMPI_"))}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(FAKE), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=full_env,
    )


def column(stdout: str, name: str) -> list[float]:
    rows = _parse(A2A, stdout)
    return [row[name] for row in rows]


# --- the fake Blink: output the real parser accepts --------------------------------------------


@requires_wrappers_checkout
@pytest.mark.parametrize("gpu", [False, True], ids=["cpu", "gpu"])
@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_fake_blink_output_is_accepted_by_the_wrapper_parser(program: str, gpu: bool) -> None:
    args = [
        program,
        "-msgsize",
        "4096",
        "-iter",
        "6",
        "-warmup",
        "2",
        "-maxsamples",
        "4",
        "-grty",
        "1",
    ]
    started = time.monotonic()
    result = run_fake(*args, *(["-gpu"] if gpu else []))
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < 1.0, f"{elapsed:.2f} s with the default NPLB_FAKE_SLEEP"
    lines = result.stdout.splitlines()
    assert (GPU_LINE in lines) is gpu
    assert HEADER in lines
    assert lines[-1] == "Ran 8 iterations. Measured 4 iterations."
    data = _parse(PROGRAMS[program], result.stdout)
    assert len(data) == 4  # rows == the trailer count: min(-iter, -maxsamples)
    for row in data:
        assert list(row) == METRICS
        avg, low, high, median, main = row.values()
        assert 0 < low <= median <= high and low < avg < high and main > 0


def test_fake_blink_keeps_1000_samples_by_default_like_blink() -> None:
    result = run_fake("alltoall_comm_only", "-iter", "1500", "-warmup", "10", NPLB_FAKE_SLEEP="0")
    assert result.returncode == 0
    assert result.stdout.splitlines()[-1] == "Ran 1510 iterations. Measured 1000 iterations."
    assert len(result.stdout.splitlines()) == 1000 + 3  # banner, header, rows, trailer


@requires_wrappers_checkout
def test_fake_blink_numbers_repeat_grow_with_size_and_with_contention() -> None:
    def mean(*args: str, **env: str) -> float:
        out = run_fake("alltoall_comm_only", "-iter", "20", *args, NPLB_FAKE_SLEEP="0", **env)
        assert out.returncode == 0, out.stderr
        values = column(out.stdout, "Avg-Duration")
        return sum(values) / len(values)

    small = mean("-msgsize", "1024")
    assert mean("-msgsize", "1024") == small  # the same table on a rerun
    assert mean("-msgsize", "1048576") > 5 * small
    contended = mean("-msgsize", "1024", NPLB_FAKE_CONTENTION="1")
    assert 1.4 < contended / small < 1.8
    assert mean("-msgsize", "1024", NPLB_FAKE_CONTENTION="0") == small


def test_fake_blink_only_rank_zero_prints() -> None:
    result = run_fake("allreduce_b", "-iter", "3", NPLB_FAKE_SLEEP="0", OMPI_COMM_WORLD_RANK="1")
    assert result.returncode == 0
    assert result.stdout == ""
    first = run_fake("allreduce_b", "-iter", "3", NPLB_FAKE_SLEEP="0", OMPI_COMM_WORLD_RANK="0")
    assert HEADER in first.stdout.splitlines()


def test_fake_blink_aggressor_runs_until_sigterm_then_exits_zero() -> None:
    args = ["-msgsize", "131072", "-endl", "-warmup", "0", "-grty", "1", "-gpu"]
    args += ["-blength", "10ms", "-bpause", "10ms"]
    proc = subprocess.Popen(
        [sys.executable, str(FAKE), "alltoall_comm_only", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)  # long enough for the interpreter to start and install its handler
        assert proc.poll() is None, "the aggressor ended before it was stopped"
        proc.terminate()  # our own child: CRAB's stop signals the process group the same way
        out, err = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, err
    assert out == ""


# --- the fake Blink: usage errors --------------------------------------------------------------


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_fake_blink_without_flags_is_a_usage_error(program: str) -> None:
    result = run_fake(program)
    assert result.returncode == 2
    assert "usage:" in result.stderr and "-iter" in result.stderr and "-endl" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "args",
    [
        ["no_such_program", "-iter", "3"],
        [],
        ["alltoall_comm_only", "-iter", "3", "-bogus"],
        ["alltoall_comm_only", "-iter", "0"],
        ["alltoall_comm_only", "-iter", "x"],
    ],
    ids=["unknown-program", "no-program", "unknown-flag", "zero-iter", "bad-number"],
)
def test_fake_blink_bad_calls_exit_2(args: list[str]) -> None:
    result = run_fake(*args, NPLB_FAKE_SLEEP="0")
    assert result.returncode == 2
    assert "usage:" in result.stderr


# --- the page check of rehearse.sh -------------------------------------------------------------


def page_ok(tmp_path: Path, text: str) -> bool:
    """The exit status of the script's `page_ok` function on a file holding `text`."""
    page = tmp_path / "page.txt"
    page.write_text(text, encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", 'source "$1" --source-only && page_ok "$2"', "_", str(REHEARSE), str(page)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stderr == ""
    return result.returncode == 0


def test_page_check_accepts_a_page_of_38_lines_of_100_columns(tmp_path: Path) -> None:
    assert page_ok(tmp_path, ("x" * 100 + "\n") * 38)
    assert page_ok(tmp_path, "one line\n")


def test_page_check_refuses_39_lines(tmp_path: Path) -> None:
    assert not page_ok(tmp_path, ("x" * 10 + "\n") * 39)


def test_page_check_refuses_a_101_column_line(tmp_path: Path) -> None:
    assert not page_ok(tmp_path, "short\n" + "x" * 101 + "\nshort\n")


def test_page_check_refuses_an_empty_page(tmp_path: Path) -> None:
    assert not page_ok(tmp_path, "")


def test_page_check_refuses_a_missing_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" --source-only && page_ok "$2"',
            "_",
            str(REHEARSE),
            str(tmp_path / "none"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0


def test_source_only_defines_the_functions_and_runs_nothing(tmp_path: Path) -> None:
    command = (
        'source "$1" --source-only && declare -F page_ok stage_setup stage_pages main && ls "$2"'
    )
    result = subprocess.run(
        ["bash", "-c", command, "_", str(REHEARSE), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""  # no stage narration
    assert "page_ok" in result.stdout


def run_function(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    """Run bash `body` after sourcing the script; the scratch variables point into tmp_path."""
    script = f'source "$1" --source-only\nSCRATCH="$2"\nLOGS="$2/log"\n{body}'
    return subprocess.run(
        ["bash", "-c", script, "_", str(REHEARSE), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_failing_stage_shows_its_last_15_lines_and_names_the_stage(tmp_path: Path) -> None:
    (tmp_path / "log").mkdir()
    lines = "".join(f"output line {i}\n" for i in range(1, 21))
    (tmp_path / "log" / "pack.txt").write_text(lines, encoding="utf-8")
    result = run_function(tmp_path, 'STAGE=pack\nfail "sha256sum -c failed"')
    assert result.returncode == 1
    assert result.stdout == "REHEARSAL FAILED at pack: sha256sum -c failed\n"
    shown = [line for line in result.stderr.splitlines() if line.startswith("output line")]
    assert shown == [f"output line {i}" for i in range(6, 21)]  # the last 15
    assert f"scratch kept: {tmp_path}" in result.stderr


def test_the_next_row_to_run_skips_the_rows_that_ran_and_the_ones_named(tmp_path: Path) -> None:
    (tmp_path / "schedule.tsv").write_text(
        "n\tfile\n1\ta.json\n2\tb.json\n3\tc.json\n4\td.json\n", encoding="utf-8"
    )
    body = f'CAMPAIGN_DIR="{tmp_path}"\nRAN_ROWS=(1 2)\nfirst_unrun_row\nfirst_unrun_row 3\n'
    body += "RAN_ROWS=(1 2 3 4)\nfirst_unrun_row\n"
    result = run_function(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["3", "4"]  # the last call finds no row and prints nothing


# --- the command line --------------------------------------------------------------------------


def test_help_prints_the_usage_and_exits_0() -> None:
    result = subprocess.run(
        ["bash", str(REHEARSE), "--help"], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0
    assert result.stdout.startswith("usage: rehearse.sh")
    assert "--keep" in result.stdout and "REHEARSAL OK" in result.stdout


def test_an_unknown_flag_exits_2_and_runs_nothing(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(REHEARSE), "--bogus"], capture_output=True, text=True, timeout=30, cwd=tmp_path
    )
    assert result.returncode == 2
    assert "unknown flag --bogus" in result.stderr
    assert "stage" not in result.stderr  # no stage started
    assert result.stdout == ""


# --- static guard ------------------------------------------------------------------------------


def test_the_script_never_signals_or_deletes_anything() -> None:
    source = REHEARSE.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"kill|scancel|pkill|killpg", source, re.IGNORECASE)
    # no deletion at all: a finished scratch folder is moved to local/rehearsal-archive/
    assert not re.search(r"\brm\b|\bunlink\b|\brmdir\b|find .*-delete|shutil\.rmtree", code)
    # every folder the script makes or moves is the scratch folder or the archive, both under local/
    assert "SCRATCH=$CHECKOUT/local/rehearsal-$(date +%Y%m%d-%H%M%S)" in code
    assert "ARCHIVE=$CHECKOUT/local/rehearsal-archive" in code
    assert 'mv "$SCRATCH" "$ARCHIVE/"' in code
    assert code.count("mv ") == 1
