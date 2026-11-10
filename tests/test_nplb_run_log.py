"""The campaign bookkeeping (examples/nplb/run_log.py): `campaign_runs.tsv`, `nplb next`, `nplb queue`.

The module is run as a subprocess (its CLI is the contract), through its pure functions and through
the dispatcher in a fake checkout. Schedules are small hand-made files in the planner's real header.
`squeue` is a fake script on PATH. Nothing starts a job or sends a signal; every file is under
`tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
RUN_LOG_PY = NPLB / "run_log.py"
PAGES_PY = NPLB / "pages.py"
SCHEDULE_HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)
RUNS_HEADER = "n\tstate\tblock\tlayout\tvictim\tjob\tdata_dir\tsubmitted\tstatus\telapsed_s\tnote"
NOW = "2026-11-10T12:00:00"
SQUEUE_FORMAT = "%i %j %T %M %l %R"
B0, B1, B2, B3 = "nplb0_ar0_sr0", "nplb1_ar0_sr1", "nplb1_ar0_sr0", "nplb1_ar1_sr1"


def _load(name: str) -> ModuleType:
    """Import a script from examples/nplb (not a package) under its own file name."""
    if str(NPLB) not in sys.path:
        sys.path.insert(0, str(NPLB))
    spec = importlib.util.spec_from_file_location(name, NPLB / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run_log = _load("run_log")
plan = _load("campaign_plan")

# (tier, state label or "-", victim, est_min): the small schedule of most tests, 6 rows
SIX = [
    ("1", B0, "a2a", 5.0),
    ("1", B0, "agtr", 6.0),
    ("1", B0, "ardc", 4.0),
    ("1", B1, "a2a", 5.3),
    ("1", B1, "agtr", 6.0),
    ("2", B1, "ardc", 3.5),
]


def schedule_text(specs: list[tuple[str, str, str, float]]) -> str:
    lines = [SCHEDULE_HEADER]
    cum = 0.0
    for n, (tier, state, victim, est) in enumerate(specs, 1):
        cum += est
        bits = ["-"] * 3 if state == "-" else list(re.findall(r"\d", state))
        cells = [str(n), tier, state, *bits, "t1", "split-i", victim, f"{est:.1f}", "0", "0"]
        cells += [f"{cum / 60:.2f}", f"t1_split-i_{victim}.json"]
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def run_line(
    n: int,
    status: str,
    job: str | None = None,
    elapsed: int | str = "",
    victim: str = "a2a",
    note: str = "",
) -> str:
    cells = [str(n), B0, "t1", "split-i", victim, job or str(1000 + n), f"/data/{n}"]
    cells += ["2026-11-10T11:00:00", status, str(elapsed), note]
    return "\t".join(cells)


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "PAGE_LINES", "PAGE_COLS", "SQUEUE", "PARTITION"):
        env.pop(var, None)
    env["WORK"] = str(tmp_path / "work")
    env["CAMPAIGN_DIR"] = str(tmp_path / "camp")
    env["NPLB_REPORT_DIR"] = str(tmp_path / "reports")
    env["NPLB_NOW"] = NOW
    env.update(extra)
    return env


def put_files(
    tmp_path: Path,
    specs: list[tuple[str, str, str, float]] | None = None,
    runs: list[str] | None = None,
    declared: tuple[str, str] | None = None,
) -> None:
    """Write schedule.tsv, campaign_runs.tsv and net_state.json (declared 12 minutes before NOW)."""
    (tmp_path / "camp").mkdir(exist_ok=True)
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "camp" / "schedule.tsv").write_text(schedule_text(specs or SIX), encoding="utf-8")
    if runs is not None:
        text = "\n".join([RUNS_HEADER, *runs]) + "\n"
        (tmp_path / "work" / "campaign_runs.tsv").write_text(text, encoding="utf-8")
    if declared is not None:
        state = {"nplb": declared[0], "ar": declared[1], "declared_at": "2026-11-10T11:48:00"}
        (tmp_path / "work" / "net_state.json").write_text(json.dumps(state), encoding="utf-8")


def run_py(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUN_LOG_PY), *args],
        env=env_for(tmp_path, **env),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def next_lines(tmp_path: Path, **env: str) -> list[str]:
    result = run_py(tmp_path, "next", **env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.splitlines()


def sample_run(n: int, job: str, status: str = "SUBMITTED", **extra: object) -> object:
    fields: dict[str, object] = {
        "n": n,
        "state": B0,
        "block": "t1",
        "layout": "split-i",
        "victim": "a2a",
        "job": job,
        "data_dir": f"/d/{job}",
        "submitted": "2026-11-10T11:00:00",
        "status": status,
        "elapsed_s": None,
        "note": "",
    }
    fields.update(extra)
    return run_log.Run(**fields)


# --- the run log ---------------------------------------------------------------------------------


def test_schedule_header_matches_the_planner() -> None:
    assert SCHEDULE_HEADER == plan.HEADER
    assert "\t".join(run_log.SCHEDULE_COLUMNS) == plan.HEADER
    assert "\t".join(run_log.RUN_COLUMNS) == RUNS_HEADER


def test_append_and_update_runs_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "work" / "campaign_runs.tsv"
    run_log.append_run(path, sample_run(1, "1001"))
    run_log.append_run(path, sample_run(2, "1002", victim="agtr"))
    first = RUNS_HEADER + "\n"
    first += "1\t" + B0 + "\tt1\tsplit-i\ta2a\t1001\t/d/1001\t2026-11-10T11:00:00\tSUBMITTED\t\t\n"
    second = "2\t" + B0 + "\tt1\tsplit-i\tagtr\t1002\t/d/1002\t2026-11-10T11:00:00\tSUBMITTED\t\t\n"
    assert path.read_text(encoding="utf-8") == first + second

    seen: list[str] = []
    real_replace = os.replace

    def replace(src: object, dst: object) -> None:
        seen.append(
            Path(str(dst)).read_text(encoding="utf-8")
        )  # the final path is still the old file
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(run_log.os, "replace", replace)
    run_log.update_run(path, "1001", status="OK", elapsed_s=300, note="all fine")
    assert seen == [first + second]
    done = (
        "1\t" + B0 + "\tt1\tsplit-i\ta2a\t1001\t/d/1001\t2026-11-10T11:00:00\tOK\t300\tall fine\n"
    )
    assert path.read_text(encoding="utf-8") == RUNS_HEADER + "\n" + done + second
    assert list(path.parent.glob("*.tmp")) == []

    # two updates in a row: every other column stays byte-identical
    run_log.update_run(path, "1002", note="x")
    run_log.update_run(path, "1002", status="FAIL", elapsed_s=12)
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").split("\n")[1:3]]
    assert rows[0] == done.rstrip("\n").split("\t")
    assert rows[1][:8] == second.split("\t")[:8]
    assert rows[1][8:] == ["FAIL", "12", "x"]
    assert list(path.parent.glob("*.tmp")) == []
    assert not any(line.endswith(" ") for line in path.read_text().split("\n"))

    with pytest.raises(KeyError, match="no-such-job"):
        run_log.update_run(path, "no-such-job", status="OK")
    with pytest.raises(ValueError, match="BOGUS"):
        run_log.update_run(path, "1001", status="BOGUS")

    run_log.append_run(path, sample_run(3, "1003", note="a\tb\nc\r\n"))
    assert run_log.read_runs(path)[-1].note == "a b c"
    assert path.read_text(encoding="utf-8").count("\n") == 4  # header + 3 rows, nothing split


def test_next_row_picks_the_first_unattempted_row(tmp_path: Path) -> None:
    put_files(
        tmp_path,
        runs=[
            run_line(1, "OK", elapsed=300),
            run_line(2, "WARN", elapsed=400),
            run_line(3, "FAIL", elapsed=50),
            run_line(5, "CANCELLED"),
        ],
        declared=("off", "off"),
    )
    lines = next_lines(tmp_path)
    assert lines[0].startswith("VERDICT: NEXT row 4/6: ")
    assert any(line.startswith("to retry") for line in lines)
    assert any(re.match(r"\s+row 3 ", line) for line in lines)
    assert not any(re.match(r"\s+row 5 ", line) for line in lines)

    schedule = run_log.read_schedule(tmp_path / "camp" / "schedule.tsv")
    runs = run_log.read_runs(tmp_path / "work" / "campaign_runs.tsv")
    assert run_log.next_row(schedule, runs).n == 4
    assert [r.n for r in run_log.retry_rows(schedule, runs)] == [3]
    assert run_log.next_row(schedule, runs[:1]).n == 2

    # a cancelled attempt counts as none; an empty or missing runs file starts at row 1
    only_cancelled = [r for r in runs if r.status == "CANCELLED"]
    assert run_log.next_row(schedule, only_cancelled).n == 1
    assert run_log.next_row(schedule, []).n == 1
    (tmp_path / "work" / "campaign_runs.tsv").unlink()
    assert next_lines(tmp_path)[0].startswith("VERDICT: NEXT row 1/6: ")

    put_files(tmp_path, runs=[run_line(n, "OK", elapsed=300) for n in range(1, 7)])
    done = next_lines(tmp_path)
    assert done[0] == "VERDICT: DONE all 6 rows"
    assert not any(line.startswith("then:") for line in done)
    assert run_log.next_row(schedule, [sample_run(n, str(n), "OK") for n in range(1, 7)]) is None

    # every row attempted, one failed: nothing new to run, the failed row is named
    put_files(
        tmp_path,
        runs=[run_line(n, "OK", elapsed=300) for n in (1, 2, 3, 4, 5)] + [run_line(6, "FAIL")],
    )
    failed = next_lines(tmp_path)
    assert failed[0].startswith("VERDICT: RETRY 1 failed row, nothing new to run")


def test_a_submitted_attempt_blocks_next(tmp_path: Path) -> None:
    put_files(
        tmp_path,
        runs=[run_line(1, "OK", elapsed=300), run_line(2, "SUBMITTED", job="4242")],
        declared=("off", "off"),
    )
    lines = next_lines(tmp_path)
    assert lines[0] == "VERDICT: RUNNING row 2 (job 4242): follow it with: nplb follow"
    assert not any(line.startswith(("then:", "needs", "MISMATCH")) for line in lines)
    assert "nplb run 3" not in "\n".join(lines)
    assert any(line.startswith("tier 1") for line in lines)  # progress is still shown


def test_estimate_uses_the_measured_ratio(tmp_path: Path) -> None:
    runs = [run_line(1, "OK", elapsed=600), run_line(2, "OK", elapsed=720)]
    put_files(tmp_path, runs=runs, declared=("off", "off"))
    schedule = run_log.read_schedule(tmp_path / "camp" / "schedule.tsv")
    found = run_log.estimate_remaining(
        schedule, run_log.read_runs(tmp_path / "work" / "campaign_runs.tsv")
    )
    assert found.ratio == pytest.approx(2.0)
    assert found.finished == 2
    assert found.remaining_h == pytest.approx((4.0 + 5.3 + 6.0 + 3.5) * 2.0 / 60)
    lines = next_lines(tmp_path)
    assert any(line.startswith("remaining  0.6 h") and "ratio 2.00" in line for line in lines)
    assert not any("no finished run yet" in line for line in lines)
    assert any("not included" in line for line in lines)

    put_files(tmp_path, runs=[])
    empty = run_log.estimate_remaining(schedule, [])
    assert empty.ratio == 1.0 and empty.finished == 0
    assert empty.remaining_h == pytest.approx(29.8 / 60)
    lines = next_lines(tmp_path)
    assert any(
        line.startswith("remaining  0.5 h") and "no finished run yet" in line for line in lines
    )

    # failed rows count as still to run; a running or failed attempt does not enter the ratio
    sample = [sample_run(1, "1", "OK", elapsed_s=600), sample_run(2, "2", "FAIL", elapsed_s=9999)]
    part = run_log.estimate_remaining(schedule, sample)
    assert part.ratio == pytest.approx(600 / 300)
    assert part.remaining_h == pytest.approx((6.0 + 4.0 + 5.3 + 6.0 + 3.5) * 2.0 / 60)


MISMATCH_REPORT = [
    "VERDICT: NEXT row 4/6: tier 1, nplb1_ar0_sr1, t1 split-i a2a (est 5.3 min)",
    "needs  nplb=on ar=off sr=on",
    "declared nplb=off ar=off (12 min ago)",
    "MISMATCH on the switches: paste the sheet, then declare:",
    "  nplb net sheet nplb=on ar=off",
    "  nplb net declare nplb=on ar=off",
    "  nplb net set sr=on",
    "then: nplb run 4",
]


def test_next_report_first_line_and_commands(tmp_path: Path) -> None:
    done = [
        run_line(1, "OK", elapsed=300),
        run_line(2, "OK", elapsed=300),
        run_line(3, "OK", elapsed=300),
    ]
    put_files(tmp_path, runs=done, declared=("off", "off"))
    lines = next_lines(tmp_path)
    assert lines[: len(MISMATCH_REPORT)] == MISMATCH_REPORT

    # the switches match, SR changes: only the SR command
    put_files(tmp_path, declared=("on", "off"))
    lines = next_lines(tmp_path)
    assert lines[:3] == MISMATCH_REPORT[:2] + ["declared nplb=on ar=off (12 min ago)"]
    assert lines[3:7] == [
        "declared state matches: no switch change needed",
        "set SR, run:",
        "  nplb net set sr=on",
        "then: nplb run 4",
    ]
    assert "MISMATCH" not in "\n".join(lines)

    # the switches match and SR is as the previous row left it: no command at all
    runs = done + [run_line(4, "OK", elapsed=300)]
    put_files(tmp_path, runs=runs, declared=("on", "off"))
    lines = next_lines(tmp_path)
    assert lines[0].startswith("VERDICT: NEXT row 5/6")
    assert lines[3:5] == ["declared state matches: no switch change needed", "then: nplb run 5"]

    # nothing ran yet: the SR command is listed
    put_files(tmp_path, runs=[], declared=("off", "off"))
    lines = next_lines(tmp_path)
    assert lines[3:6] == [
        "declared state matches: no switch change needed",
        "set SR, run:",
        "  nplb net set sr=off",
    ]

    # nothing declared, and an unreadable declaration
    put_files(tmp_path, runs=done)
    (tmp_path / "work" / "net_state.json").unlink(missing_ok=True)
    lines = next_lines(tmp_path)
    assert lines[2] == "no switch state declared"
    assert "  nplb net declare nplb=on ar=off" in lines
    assert "  nplb net set sr=on" in lines
    (tmp_path / "work" / "net_state.json").write_text("{not json", encoding="utf-8")
    assert next_lines(tmp_path)[2].startswith("declared state unreadable")


def test_together_state_adds_the_note(tmp_path: Path) -> None:
    specs = [("1", B3, "a2a", 5.0)]
    put_files(tmp_path, specs=specs, runs=[], declared=("on", "on"))
    lines = next_lines(tmp_path)
    assert lines[1] == "needs  nplb=on ar=on sr=on"
    assert "note   NPLB+AR together is untested on these switches" in lines
    assert any(
        "if the switch refuses this state, skip its rows: they are the last of each tier" in x
        for x in lines
    )
    assert "skip-state" not in "\n".join(lines)


def test_local_rows_need_no_network_state(tmp_path: Path) -> None:
    put_files(tmp_path, specs=[("L", "-", "a2a", 2.0), ("L", "-", "agtr", 2.0)], runs=[])
    lines = next_lines(tmp_path)
    assert lines[0].startswith("VERDICT: NEXT row 1/2: tier L, -, ")
    assert lines[1] == "needs  no network state (local rehearsal)"
    assert lines[2] == "then: nplb run 1"


def test_progress_block(tmp_path: Path) -> None:
    specs = [("1", B0, "a2a", 5.0)] * 24 + [("2", B1, "agtr", 10.0)] * 12
    runs = [run_line(n, "OK", elapsed=600) for n in range(1, 13)]
    runs += [run_line(13, "WARN", elapsed=600), run_line(14, "FAIL", elapsed=120)]
    runs += [run_line(15, "SUBMITTED"), run_line(16, "CANCELLED", elapsed=5000)]
    put_files(tmp_path, specs=specs, runs=runs, declared=("off", "off"))
    lines = next_lines(tmp_path)
    assert "tier 1  13/24 done  1 failed  2.2 h elapsed  1.8 h left" in lines
    assert "tier 2  0/12 done  0 failed  0.0 h elapsed  4.0 h left" in lines
    assert "total   13/36 done  1 failed  2.2 h elapsed  5.8 h left" in lines
    assert not any(line.startswith("tier 3") for line in lines)
    assert lines[0].startswith("VERDICT: RUNNING row 15 ")

    schedule = run_log.read_schedule(tmp_path / "camp" / "schedule.tsv")
    runs_read = run_log.read_runs(tmp_path / "work" / "campaign_runs.tsv")
    first, second = run_log.progress(schedule, runs_read)
    assert (first.tier, first.rows, first.done, first.failed) == ("1", 24, 13, 1)
    assert first.elapsed_s == 13 * 600 + 120
    assert (second.tier, second.rows, second.done, second.failed, second.elapsed_s) == (
        "2",
        12,
        0,
        0,
        0,
    )


def test_report_fits_a_page(tmp_path: Path) -> None:
    specs = [(str(1 + i // 21), B0 if i % 2 else B1, "a2a", 5.0 + i % 3) for i in range(84)]
    runs = [run_line(n, "OK", elapsed=400 + n) for n in range(1, 30)]
    runs += [run_line(n, "FAIL", elapsed=30) for n in range(30, 45)]
    runs += [run_line(45, "SUBMITTED")]
    put_files(tmp_path, specs=specs, runs=runs, declared=("on", "off"))
    result = run_py(tmp_path, "next")
    assert result.returncode == 0, result.stderr
    paged = subprocess.run(
        [sys.executable, str(PAGES_PY), "emit", "next"],
        input=result.stdout,
        env=env_for(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert paged.returncode == 0, paged.stderr
    lines = paged.stdout.splitlines()
    assert re.match(r"next \| page 1/\d+", lines[0])
    assert lines[1].startswith("VERDICT: RUNNING row 45 ")
    assert len(lines) <= 38 and max(len(line) for line in lines) <= 100
    report = result.stdout.splitlines()
    assert max(len(line) for line in report) <= 100
    assert (
        sum(1 for line in report if re.match(r"\s+row \d+ ", line)) <= 6
    )  # the retry list is capped
    assert any(line.startswith("total   ") for line in report)


# --- queue (through the dispatcher: the squeue call lives in bash) -------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


FAKE_SQUEUE = """#!/usr/bin/env bash
printf '%s\\n' "$@" >"$SQUEUE_ARGS"
if [[ ${FAKE_SQUEUE_MODE:-ok} == fail ]]; then
    echo "squeue: error: invalid partition specified" >&2
    echo "second line" >&2
    exit 1
fi
if [[ ${FAKE_SQUEUE_MODE:-ok} == empty ]]; then
    exit 0
fi
echo "1001 nplb_t1_split-i_a2a RUNNING 3:21 20:00 node1"
echo "1002 nplb_t1_split-i_agtr PENDING 0:00 20:00 (Priority)"
exit 0
"""


def run_nplb(tmp_path: Path, chk: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    if not bin_dir.exists():
        bin_dir.mkdir()
        (bin_dir / "squeue").write_text(FAKE_SQUEUE, encoding="utf-8")
        (bin_dir / "squeue").chmod(0o755)
    merged = env_for(tmp_path, **env)
    merged["PATH"] = f"{bin_dir}:{merged['PATH']}"
    merged.setdefault("SQUEUE_ARGS", str(tmp_path / "squeue_args.txt"))
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=merged,
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def content(stdout: str) -> list[str]:
    """The report lines of a paged print: no header, no footer."""
    return stdout.splitlines()[1:-1]


def test_queue_report(tmp_path: Path, checkout: Path) -> None:
    runs = [
        run_line(n, "OK", job=str(2000 + n), elapsed=300 + n, victim="agtr") for n in range(1, 11)
    ]
    put_files(tmp_path, runs=runs)
    result = run_nplb(tmp_path, checkout, "queue")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("queue | page 1/")
    lines = content(result.stdout)
    assert lines[0].startswith("VERDICT: QUEUE 2 jobs in h100_4nodes")
    assert "1001 nplb_t1_split-i_a2a RUNNING 3:21 20:00 node1" in lines
    assert "1002 nplb_t1_split-i_agtr PENDING 0:00 20:00 (Priority)" in lines
    args = (tmp_path / "squeue_args.txt").read_text(encoding="utf-8").splitlines()
    assert args == ["-h", "-p", "h100_4nodes", "-o", SQUEUE_FORMAT]
    at = lines.index("last runs:")
    table = lines[at + 1 :]
    assert table[0].split() == ["n", "state", "block", "victim", "job", "status", "elapsed"]
    assert len(table) == 1 + 8
    assert table[1].split() == ["10", B0, "t1", "agtr", "2010", "OK", "310s"]
    assert table[8].split()[0] == "3"  # newest first, eight rows
    assert "2002" not in "\n".join(table)

    other = run_nplb(tmp_path, checkout, "queue", PARTITION="gpu_other")
    assert other.returncode == 0
    args = (tmp_path / "squeue_args.txt").read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["-h", "-p", "gpu_other"]
    assert "in gpu_other" in content(other.stdout)[0]

    # no queue entries and an empty log
    (tmp_path / "work" / "campaign_runs.tsv").unlink()
    empty = run_nplb(tmp_path, checkout, "queue", FAKE_SQUEUE_MODE="empty")
    lines = content(empty.stdout)
    assert empty.returncode == 0
    assert lines[0].startswith("VERDICT: QUEUE empty in h100_4nodes")
    assert lines[-2:] == ["last runs:", "no runs yet"]

    # squeue fails: its first error line, exit 0, the run table still there
    put_files(tmp_path, runs=runs)
    failing = run_nplb(tmp_path, checkout, "queue", FAKE_SQUEUE_MODE="fail")
    lines = content(failing.stdout)
    assert failing.returncode == 0
    assert lines[0].startswith("VERDICT: WARN queue unknown")
    assert "squeue: error: invalid partition specified" in lines
    assert "second line" not in lines
    assert "last runs:" in lines

    # no squeue installed
    missing = run_nplb(tmp_path, checkout, "queue", SQUEUE="squeue-not-installed-here")
    lines = content(missing.stdout)
    assert missing.returncode == 0
    assert "squeue not found: cannot show the queue" in lines
    assert "last runs:" in lines and lines[lines.index("last runs:") + 1].startswith("n ")


def test_queue_cuts_long_lines() -> None:
    long_line = "1001 " + "x" * 200
    cut = run_log.cut_line(long_line)
    assert len(cut) == 100 and cut.endswith("~")
    assert run_log.cut_line("short") == "short"
    report = run_log.queue_report([], run_log.Squeue([long_line], ""), "p")
    assert max(len(line) for line in report) <= 100


def test_missing_or_bad_schedule(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    result = run_py(tmp_path, "next")
    assert result.returncode == 2
    assert "no schedule: run: nplb campaign" in result.stderr
    assert result.stdout == ""

    put_files(tmp_path, runs=[])
    path = tmp_path / "camp" / "schedule.tsv"
    path.write_text(path.read_text().replace("\tstate\t", "\tstat\t", 1), encoding="utf-8")
    result = run_py(tmp_path, "next")
    assert result.returncode == 2
    assert "column 3" in result.stderr and "'stat'" in result.stderr and "'state'" in result.stderr

    put_files(tmp_path, runs=[])
    runs = tmp_path / "work" / "campaign_runs.tsv"
    runs.write_text(runs.read_text().replace("\tjob\t", "\tjobid\t", 1), encoding="utf-8")
    result = run_py(tmp_path, "next")
    assert result.returncode == 2
    assert str(runs) in result.stderr and "'jobid'" in result.stderr

    put_files(tmp_path, runs=[run_line(1, "OK", elapsed=3).replace("OK", "DONE")])
    bad_status = run_py(tmp_path, "next")
    assert bad_status.returncode == 2 and "DONE" in bad_status.stderr and ":2:" in bad_status.stderr

    # a bad NPLB_NOW is a clear exit 2, and queue does not need the schedule
    put_files(tmp_path, runs=[], declared=("off", "off"))
    clock = run_py(tmp_path, "next", NPLB_NOW="yesterday")
    assert clock.returncode == 2 and "NPLB_NOW" in clock.stderr
    (tmp_path / "camp" / "schedule.tsv").unlink()
    assert run_py(tmp_path, "queue").returncode == 0


def test_dispatcher_next_and_queue(tmp_path: Path, checkout: Path) -> None:
    put_files(tmp_path, runs=[run_line(1, "OK", elapsed=300)], declared=("off", "off"))
    shown = run_nplb(tmp_path, checkout, "next")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    lines = shown.stdout.splitlines()
    assert lines[0].startswith("next | page 1/")
    assert lines[1].startswith("VERDICT: NEXT row 2/6")
    assert len(lines) <= 38 and max(len(line) for line in lines) <= 100
    queued = run_nplb(tmp_path, checkout, "queue")
    assert queued.returncode == 0, queued.stdout + queued.stderr
    assert queued.stdout.splitlines()[0].startswith("queue | page 1/")
    saved = sorted(p.name for p in (tmp_path / "reports").iterdir() if p.name.endswith(".txt"))
    assert [name.split("_", 1)[1] for name in saved] == ["next.txt", "queue.txt"]

    (tmp_path / "camp" / "schedule.tsv").unlink()
    bad = run_nplb(tmp_path, checkout, "next")
    assert bad.returncode == 2
    assert "no schedule: run: nplb campaign" in bad.stderr

    helped = run_nplb(tmp_path, checkout, "help")
    help_lines = helped.stdout.splitlines()
    assert any(line.startswith("  next") for line in help_lines)
    assert any(line.startswith("  queue") for line in help_lines)
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100


def test_python_310_safe(tmp_path: Path) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    put_files(tmp_path, runs=[run_line(1, "OK", elapsed=300)], declared=("off", "off"))
    result = subprocess.run(
        [python310, str(RUN_LOG_PY), "next"],
        env=env_for(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("VERDICT: NEXT row 2/6")
