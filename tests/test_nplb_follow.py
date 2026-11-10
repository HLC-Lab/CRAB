"""`nplb follow` (examples/nplb/follow.py): how a CRAB job ended on a
cluster with no Slurm accounting, narrated live, then summarised.

Before: the only code that could say how a job ended lived in bash inside the smoke step, and the owner's
smoke waited 6 minutes after the job ended because `crab status` said UNKNOWN for every finished job.
The decision functions are tested on real excerpts of the October 2026 runs (tests/fixtures/nplb); the
loop runs as a subprocess against fake `squeue` and `scontrol` scripts and a log that a fake `squeue`
grows between polls. Nothing starts or cancels a job; every file is under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
NPLB = REPO / "examples" / "nplb"
FIX = REPO / "tests" / "fixtures" / "nplb"
FOLLOW_PY = NPLB / "follow.py"
PAGES_PY = NPLB / "pages.py"
A2A_RUN = "gpu_a2a_comm_same_leaf_2026-10-29_16-04-13-621184"
AG_RUN = "gpu_ag_comm_same_leaf_2026-10-29_16-35-53-182977"
NOW = "2026-11-10T12:00:00"
SUBMITTED = "2026-11-10T11:00:00"
STAMP_NOW = "12:00:00"
B0 = "nplb0_ar0_sr0"
SCHEDULE_HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)
RUNS_HEADER = "n\tstate\tblock\tlayout\tvictim\tjob\tdata_dir\tsubmitted\tstatus\telapsed_s\tnote"


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


follow = _load("follow")
follow_log = follow  # one module: the decision functions and the loop


# --- log lines in the engine's real shape ------------------------------------------------------------


def crab_line(
    stamp: str,
    msg: str,
    ctx: tuple[str, ...] = (),
    level: str = "INFO",
    source: str = "CRAB",
    color: bool = True,
) -> str:
    """One worker log line as the engine writes it: dim stamp, coloured level and source, then
    `│ part` for every context part and two blanks before the message."""

    def dim(text: str) -> str:
        return f"\x1b[2m{text}\x1b[0m" if color else text

    lvl = f"\x1b[32m{level:<5}\x1b[0m" if color else f"{level:<5}"
    src = f"\x1b[34m{source}\x1b[0m" if color else source
    parts = "".join(f" {dim('│')} {dim(part)}" for part in ctx)
    return f"{dim('[' + stamp + ']')} {lvl} {src}{parts}  {msg}"


def app_lines(
    exp: str, app: int, t: int, ok: bool = True, code: int = 0, run: int = 1
) -> list[str]:
    """Launched, then FINISHED or FAILED for one app; `t` is seconds into the hour."""
    ctx = (exp, f"Run {run}", f"App {app}")
    start = f"12:{t // 60:02d}:{t % 60:02d}"
    end = f"12:{(t + 5) // 60:02d}:{(t + 5) % 60:02d}"
    lines = [crab_line(start, f"Launched App {app}  PID={1000 + app}  nodes=['nodeA']", ctx)]
    if ok:
        lines.append(crab_line(end, "FINISHED  exit=0", ctx))
    else:
        lines.append(crab_line(end, f"FAILED  exit={code}", ctx, level="ERROR"))
    return lines


def synth_log(
    exps: list[str], apps: int, failed: set[tuple[str, int]] | None = None, tail: bool = True
) -> list[str]:
    """A whole worker log: setup, `exps` x `apps` apps (those in `failed` fail), and the ending."""
    failed = failed or set()
    lines = [
        crab_line("12:00:00", "Worker mode detected  workdir=/home/user/d"),
        crab_line("12:00:01", "Worker started"),
        crab_line("12:00:01", "Allocated 2 node(s)"),
    ]
    t = 2
    for k, exp in enumerate(exps, 1):
        lines.append(
            crab_line(
                f"12:{t // 60:02d}:{t % 60:02d}", f"Starting experiment [{k}/{len(exps)}]: {exp}"
            )
        )
        lines.append(crab_line(f"12:{t // 60:02d}:{t % 60:02d}", "Started", (exp, "Run 1")))
        for app in range(apps):
            lines += app_lines(exp, app, t, ok=(exp, app) not in failed, code=17)
            t += 6
        lines.append(
            crab_line(f"12:{t // 60:02d}:{t % 60:02d}", f"Data saved to /home/user/d/{exp}", (exp,))
        )
    if tail:
        lines.append(crab_line(f"12:{t // 60:02d}:{t % 60:02d}", "All experiments finished"))
        lines.append(
            crab_line(f"12:{t // 60:02d}:{t % 60:02d}", "Engine run finished  elapsed=0:02:00")
        )
    return lines


def write_log(data_dir: Path, lines: list[str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "slurm_output.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def fixture_run(root: Path, tag: str) -> Path:
    """A finished real run: the engine's own log lines, metadata.csv in the data root, and for the
    all-to-all run the error log of the failed app of ppn4_alone."""
    name = A2A_RUN if tag == "a2a" else AG_RUN
    data_dir = root / "data" / "nplb_h100_gpu" / name
    data_dir.mkdir(parents=True)
    shutil.copy(FIX / f"{tag}_run1_crab_lines.txt", data_dir / "slurm_output.log")
    shutil.copy(FIX / f"{tag}_run1_metadata.csv", data_dir.parent / "metadata.csv")
    if tag == "a2a":
        (data_dir / "ppn4_alone").mkdir()
        shutil.copy(FIX / "a2a_run1_error_app_10.log", data_dir / "ppn4_alone" / "error_app_10.log")
    return data_dir


# --- the pure decision functions ----------------------------------------------------------------------


@pytest.mark.parametrize("tag,elapsed", [("a2a", "0:10:48"), ("ag", "0:12:21")])
def test_log_markers_on_real_fixtures(tag: str, elapsed: str) -> None:
    raw = (FIX / f"{tag}_run1_log_tail.txt").read_text(encoding="utf-8")
    assert "\x1b[" in raw  # the fixtures keep the real colour codes
    line = follow_log.last_line(follow_log.strip_ansi(raw))
    assert "\x1b" not in line
    assert line.endswith(f"Engine run finished  elapsed={elapsed}")
    assert follow_log.classify_last_line(line) == "ok"
    head = (FIX / f"{tag}_run1_log_head.txt").read_text(encoding="utf-8")
    first = follow_log.strip_ansi(head).splitlines()[0]
    assert first.startswith("[16:") and "Worker mode detected" in first


@pytest.mark.parametrize(
    "msg,level,expected",
    [
        ("Worker fatal error: boom", "ERROR", "fail"),
        ("FATAL: out of memory", "ERROR", "fail"),
        ("Worker terminated (SIGTERM)", "WARN", "terminated"),
        ("Worker interrupted by user", "WARN", "terminated"),
        ("All experiments finished", "INFO", "none"),
        ("FINISHED  exit=0", "INFO", "none"),
    ],
)
def test_log_markers_on_synthetic_endings(msg: str, level: str, expected: str) -> None:
    line = follow_log.strip_ansi(crab_line("12:00:00", msg, level=level))
    assert follow_log.classify_last_line(line) == expected
    assert follow_log.classify_last_line("") == "none"
    # the last NON-EMPTY line decides, whatever follows it
    text = line + "\n\n   \n"
    assert follow_log.classify_last_line(follow_log.last_line(text)) == expected


def decide(
    data_dir: Path,
    sc: tuple[str, str] = ("", ""),
    clocks: object = None,
    now: float = 0.0,
    stall: float = 120.0,
) -> object:
    clocks = clocks or follow_log.Clocks()
    return follow_log.decide_outcome(data_dir, sc, clocks, now, stall)


def test_real_a2a_run_is_warn_with_the_failed_experiments(tmp_path: Path) -> None:
    a2a = decide(fixture_run(tmp_path / "a", "a2a"))
    assert a2a.verdict == "WARN"
    assert a2a.src == "log"
    assert "ended OK (job log: Engine run finished)" in a2a.why
    assert "ppn4_alone, ppn8_alone" in a2a.reason()
    assert "ppn1_alone" not in a2a.reason()
    assert follow_log.metadata_failures(fixture_run(tmp_path / "a2", "a2a")) == (
        "failed",
        ["ppn4_alone", "ppn8_alone"],
    )
    ag_dir = fixture_run(tmp_path / "b", "ag")
    ag = decide(ag_dir)
    assert ag.verdict == "OK" and ag.src == "log" and ag.note == "" and ag.side == ""
    assert follow_log.metadata_failures(ag_dir) == ("clean", [])
    # the a2a rows are in the ag metadata.csv too, but they belong to another job: not this run's
    assert "a2a" in (ag_dir.parent / "metadata.csv").read_text(encoding="utf-8")

    # no metadata.csv: still OK, and the report is told the cross-check was skipped
    (ag_dir.parent / "metadata.csv").unlink()
    missing = decide(ag_dir)
    assert missing.verdict == "OK" and "cross-check skipped" in missing.side
    (ag_dir.parent / "metadata.csv").write_bytes(b"\xff\xfe\x00 not a csv \x00")
    assert decide(ag_dir).verdict in ("OK", "WARN")


@pytest.mark.parametrize(
    "state,code,verdict,src",
    [
        ("FAILED", "1:0", "FAIL", "scontrol"),
        ("TIMEOUT", "0:15", "FAIL", "scontrol"),
        ("CANCELLED", "0:0", "FAIL", "scontrol"),
        ("NODE_FAIL", "", "FAIL", "scontrol"),
        ("OUT_OF_MEMORY", "0:125", "FAIL", "scontrol"),
        ("COMPLETED", "1:0", "FAIL", "scontrol"),
        ("COMPLETED", "0:0", "OK", "log"),
        ("COMPLETED", "", "OK", "log"),
        ("", "", "OK", "log"),
    ],
)
def test_outcome_order_scontrol_then_log_then_stall(
    tmp_path: Path, state: str, code: str, verdict: str, src: str
) -> None:
    data_dir = fixture_run(tmp_path, "ag")  # the log says Engine run finished
    outcome = decide(data_dir, (state, code))
    assert (outcome.verdict, outcome.src) == (verdict, src)
    if src == "scontrol":
        assert state in outcome.why


def test_scontrol_alive_resets_the_silence_clocks(tmp_path: Path) -> None:
    data_dir = tmp_path / "run"
    write_log(data_dir, [crab_line("12:00:01", "Worker started")])
    clocks = follow_log.Clocks()
    assert decide(data_dir, ("", ""), clocks, now=0, stall=10).verdict == "UNDECIDED"
    # silent for 9 s: still undecided; scontrol says RUNNING, so the clocks restart
    assert decide(data_dir, ("RUNNING", ""), clocks, now=9, stall=10).verdict == "UNDECIDED"
    assert decide(data_dir, ("RUNNING", ""), clocks, now=18, stall=10).verdict == "UNDECIDED"
    assert decide(data_dir, ("RUNNING", ""), clocks, now=27, stall=10).verdict == "UNDECIDED"
    # scontrol forgets the job: the silence counts from the last time it said alive
    assert decide(data_dir, ("", ""), clocks, now=30, stall=10).verdict == "UNDECIDED"
    late = decide(data_dir, ("", ""), clocks, now=37.5, stall=10)
    assert late.verdict == "FAIL"


def test_parse_scontrol_job_text() -> None:
    text = (
        "JobId=4242 JobName=nplb_t1\n   UserId=user(1000) GroupId=user(1000)\n"
        "   JobState=COMPLETED Reason=None Dependency=(null)\n"
        "   RunTime=00:10:48 ExitCode=0:0\n   StdOut=/home/user/d/slurm_output.log\n"
    )
    assert follow_log.parse_scontrol(text) == ("COMPLETED", "0:0")
    assert follow_log.parse_scontrol("slurm_load_jobs error: Invalid job id") == ("", "")
    assert follow_log.parse_scontrol("JobState=TIMEOUT ExitCode=0:15") == ("TIMEOUT", "0:15")


def test_stall_rules(tmp_path: Path) -> None:
    data_dir = tmp_path / "run"
    write_log(data_dir, [crab_line("12:00:01", "Worker started"), "benchmark noise"])
    (data_dir / "slurm_error.log").write_text(
        "Traceback (most recent call last):\n  File x\nMemoryError: out of memory\n\n",
        encoding="utf-8",
    )
    clocks = follow_log.Clocks()
    assert decide(data_dir, clocks=clocks, now=100, stall=5).verdict == "UNDECIDED"
    assert decide(data_dir, clocks=clocks, now=104.9, stall=5).verdict == "UNDECIDED"
    stalled = decide(data_dir, clocks=clocks, now=105, stall=5)
    assert stalled.verdict == "FAIL" and stalled.src == "log"
    assert stalled.why == "ended without a final line (stopped early?)"
    assert "MemoryError: out of memory" in stalled.note

    # a log that is still growing is never a stall, however long the job has been gone
    clocks = follow_log.Clocks()
    for step in range(8):
        with (data_dir / "slurm_output.log").open("a", encoding="utf-8") as handle:
            handle.write(crab_line("12:00:02", f"line {step}") + "\n")
        assert decide(data_dir, clocks=clocks, now=100 + step * 3, stall=5).verdict == "UNDECIDED"

    # no log at all: the clock starts when the job is first seen outside the queue
    empty = tmp_path / "never-started"
    empty.mkdir()
    clocks = follow_log.Clocks()
    assert decide(empty, clocks=clocks, now=50, stall=5).verdict == "UNDECIDED"
    nolog = decide(empty, clocks=clocks, now=55, stall=5)
    assert nolog.verdict == "FAIL"
    assert nolog.why == "job left the queue without writing a log"

    terminated = tmp_path / "terminated"
    write_log(terminated, [crab_line("12:00:01", "Worker terminated (SIGTERM)", level="WARN")])
    stop = decide(terminated)
    assert stop.verdict == "FAIL" and stop.terminated and stop.why == "terminated (job log)"
    assert "last log line: Worker terminated (SIGTERM)" in stop.note
    fatal = tmp_path / "fatal"
    write_log(fatal, [crab_line("12:00:01", "Worker fatal error: boom", level="ERROR")])
    bad = decide(fatal)
    assert bad.verdict == "FAIL" and not bad.terminated and bad.why == "FAILED (job log)"


def test_filter_shows_only_the_engine_messages() -> None:
    shown = {
        crab_line("12:00:01", "Worker started"): "Worker started",
        crab_line("12:00:01", "Allocated 2 node(s)"): "Allocated 2 node(s)",
        crab_line("12:00:02", "Starting experiment [3/4]: ppn4_alone"): (
            "Starting experiment [3/4]: ppn4_alone"
        ),
        crab_line("12:00:03", "Started", ("ppn4_alone", "Run 1")): "ppn4_alone Run 1: Started",
        crab_line(
            "12:00:04",
            "Launched App 7  PID=718056  nodes=['nodeA', 'nodeB']",
            ("e", "Run 1", "App 7"),
        ): ("e Run 1 App 7: Launched App 7 PID=718056"),
        crab_line("12:00:05", "FINISHED  exit=0", ("e", "Run 1", "App 7")): (
            "e Run 1 App 7: FINISHED exit=0"
        ),
        crab_line("12:00:06", "FAILED  exit=17", ("e", "Run 1", "App 8"), level="ERROR"): (
            "e Run 1 App 8: FAILED exit=17"
        ),
        crab_line("12:00:07", "HARD TIMEOUT after 3600 s", ("e", "Run 1", "App 9"), level="WARN"): (
            "e Run 1 App 9: HARD TIMEOUT after 3600 s"
        ),
        crab_line("12:00:08", "Converged at run 1 (1 successful)", ("e",)): (
            "e: Converged at run 1 (1 successful)"
        ),
        crab_line("12:00:09", "Data saved to /home/user/crab/data/x/ppn4_alone", ("ppn4_alone",)): (
            "Data saved to ppn4_alone"
        ),
        crab_line("12:00:10", "All experiments finished"): "All experiments finished",
        crab_line("12:00:11", "Engine run finished  elapsed=0:00:35"): (
            "Engine run finished elapsed=0:00:35"
        ),
        crab_line("12:00:12", "Experiment e failed: boom", level="ERROR"): (
            "Experiment e failed: boom"
        ),
        crab_line("12:00:13", "disk nearly full", level="WARN"): "WARN disk nearly full",
        crab_line(
            "12:00:14", "Worker fatal error: x", level="ERROR"
        ): "ERROR Worker fatal error: x",
    }
    for raw, text in shown.items():
        assert follow_log.filter_worker_line(raw) == text, raw
    hidden = [
        crab_line("12:00:01", "Setting up...", ("e",)),
        crab_line("12:00:01", "Allocation: linear", ("e",)),
        crab_line("12:00:01", "App 0: nodeA,nodeB", ("e",)),
        crab_line("12:00:01", "Execution started", ("e",)),
        crab_line("12:00:01", "Environment loaded, starting engine"),
        # the benchmark's own output: never shown, whatever its level
        crab_line("12:00:02", "STDOUT Dump:", ("e", "Run 1", "App 3"), source="APP"),
        crab_line(
            "12:00:02",
            "All-to-all with 8 processes",
            ("e", "Run 1", "App 3"),
            level="WARN",
            source="APP",
        ),
        "[1793290272.945368] [nodeA:721074:0] ib_md.c:296  UCX  ERROR ibv_reg_mr failed: Bad address",
        "[nodeA:723473] *** MPI_ERR_INTERN: internal error",
        "crab: nodeA local rank 0 -> UCX_NET_DEVICES=mlx5_0:1",
        "",
    ]
    for raw in hidden:
        assert follow_log.filter_worker_line(raw) is None, raw


def test_the_real_logs_are_filtered_without_benchmark_noise() -> None:
    for tag in ("a2a", "ag"):
        raw = (FIX / f"{tag}_run1_crab_lines.txt").read_text(encoding="utf-8").splitlines()
        shown = [t for t in map(follow_log.filter_worker_line, raw) if t]
        assert shown[0] == "Worker started" and shown[1].startswith("Allocated ")
        assert shown[-1].startswith("Engine run finished elapsed=")
        assert not any("UCX" in t or "MCW" in t for t in shown)
        assert sum(1 for t in shown if t.startswith("Starting experiment")) == 4
    a2a = (FIX / "a2a_run1_crab_lines.txt").read_text(encoding="utf-8").splitlines()
    texts = [t for t in map(follow_log.filter_worker_line, a2a) if t]
    assert [t for t in texts if "FAILED" in t][0] == "ppn4_alone Run 1 App 10: FAILED exit=17"
    assert sum(1 for t in texts if "FAILED" in t) == 3


# --- fake Slurm and the loop --------------------------------------------------------------------------

FAKE_SQUEUE = r"""#!/usr/bin/env bash
d=$FAKE_DIR
echo "squeue $*" >>"$d/calls"
n=0
[[ -f $d/squeue.n ]] && n=$(<"$d/squeue.n")
n=$((n + 1))
echo "$n" >"$d/squeue.n"
if [[ -f $d/growth.target && -f $d/growth/$n ]]; then
    cat "$d/growth/$n" >>"$(<"$d/growth.target")"
fi
[[ -f $d/states ]] || exit 0
state=$(sed -n "${n}p" "$d/states")
[[ -n $state ]] || state=$(tail -n 1 "$d/states")
elapsed=1:45
if [[ $state == *"|"* ]]; then elapsed=${state#*|}; state=${state%%|*}; fi
case $state in
GONE) exit 0 ;;
ERR) echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1 ;;
esac
echo "$state $elapsed"
"""

FAKE_SCONTROL = r"""#!/usr/bin/env bash
d=$FAKE_DIR
echo "scontrol $*" >>"$d/calls"
if [[ -f $d/scontrol.out ]]; then
    cat "$d/scontrol.out"
    exit 0
fi
echo "slurm_load_jobs error: Invalid job id specified" >&2
exit 1
"""

FAKE_MARKER = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >>"$FAKE_DIR/forbidden"
exit 0
"""


class Lab:
    """A fake lab in tmp_path: fake squeue and scontrol, scancel/sbatch/crab that must never run."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.fake = tmp_path / "fake"
        self.fake.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("squeue", FAKE_SQUEUE), ("scontrol", FAKE_SCONTROL)):
            (self.bin / name).write_text(body, encoding="utf-8")
        for name in ("scancel", "sbatch", "crab", "sacct"):
            (self.bin / name).write_text(FAKE_MARKER, encoding="utf-8")
        for path in self.bin.iterdir():
            path.chmod(0o755)
        self.data_root = tmp_path / "data" / "nplb_h100_gpu"
        self.data_root.mkdir(parents=True)
        (tmp_path / "work").mkdir()
        (tmp_path / "camp").mkdir()
        (tmp_path / "elsewhere").mkdir()

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        for var in ("PYTHONPATH", "PAGE_LINES", "PAGE_COLS", "PARTITION", "NPLB_WORK"):
            env.pop(var, None)
        env.update(
            FAKE_DIR=str(self.fake),
            SQUEUE=str(self.bin / "squeue"),
            SCONTROL=str(self.bin / "scontrol"),
            WORK=str(self.root / "work"),
            CAMPAIGN_DIR=str(self.root / "camp"),
            NPLB_REPORT_DIR=str(self.root / "reports"),
            DATA_ROOT=str(self.data_root),
            NPLB_NOW=NOW,
            FOLLOW_POLL="0.05",
            FOLLOW_STALL="1",
            FOLLOW_HEARTBEAT="15",
        )
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env.update(extra)
        return env

    def states(self, *states: str) -> None:
        (self.fake / "states").write_text("\n".join(states) + "\n", encoding="utf-8")

    def grow(self, log: Path, steps: dict[int, list[str]]) -> None:
        """At the Nth squeue call, append those lines to `log`."""
        (self.fake / "growth").mkdir(exist_ok=True)
        (self.fake / "growth.target").write_text(str(log), encoding="utf-8")
        for call, lines in steps.items():
            (self.fake / "growth" / str(call)).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def scontrol(self, text: str) -> None:
        (self.fake / "scontrol.out").write_text(text, encoding="utf-8")

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def forbidden(self) -> list[str]:
        path = self.fake / "forbidden"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def put_files(
        self, runs: list[str] | None = None, specs: list[tuple[str, str, float]] | None = None
    ) -> None:
        lines = [SCHEDULE_HEADER]
        for n, (victim, state, est) in enumerate(specs or [("a2a", B0, 5.0), ("agtr", B0, 6.0)], 1):
            bits = re.findall(r"\d", state)
            cells = [str(n), "1", state, *bits, "t1", "split-i", victim, f"{est:.1f}", "0", "0"]
            cells += [f"{n * 0.1:.2f}", f"t1_split-i_{victim}.json"]
            lines.append("\t".join(cells))
        (self.root / "camp" / "schedule.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if runs is not None:
            text = "\n".join([RUNS_HEADER, *runs]) + "\n"
            (self.root / "work" / "campaign_runs.tsv").write_text(text, encoding="utf-8")

    def run_row(
        self,
        n: int,
        job: str,
        data_dir: Path,
        status: str = "SUBMITTED",
        victim: str = "a2a",
        elapsed: str = "",
        submitted: str = SUBMITTED,
    ) -> str:
        cells = [str(n), B0, "t1", "split-i", victim, job, str(data_dir), submitted, status]
        return "\t".join([*cells, elapsed, ""])

    def runs_text(self) -> str:
        return (self.root / "work" / "campaign_runs.tsv").read_text(encoding="utf-8")

    def follow(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FOLLOW_PY), "follow", *args],
            env=self.env(**env),
            cwd=self.root / "elsewhere",
            capture_output=True,
            text=True,
            timeout=60,
        )


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


def report_of(result: subprocess.CompletedProcess[str]) -> tuple[str, list[str]]:
    """(report name, report lines) of the python's stdout: `#report NAME` and then the report."""
    lines = result.stdout.splitlines()
    assert lines and lines[0].startswith("#report "), result.stdout + result.stderr
    return lines[0].removeprefix("#report "), lines[1:]


def stamped(stderr: str) -> list[str]:
    """The narration lines without their `[HH:MM:SS] ` stamp (every line must carry one)."""
    texts = []
    for line in stderr.splitlines():
        found = re.match(r"^\[(\d\d:\d\d:\d\d)\] (.*)$", line)
        assert found, f"narration line without a stamp: {line!r}"
        texts.append(found.group(2))
    return texts


def test_loop_follows_a_growing_log_and_ends_ok(lab: Lab) -> None:
    data_dir = lab.data_root / "run-a"
    first = [
        crab_line("12:00:01", "Worker started"),
        crab_line("12:00:01", "Allocated 2 node(s)"),
        crab_line("12:00:02", "Setting up...", ("e1",)),
    ]
    write_log(data_dir, first)
    ctx = ("e1", "Run 1", "App 0")
    lab.states("RUNNING", "RUNNING", "GONE")
    lab.grow(
        data_dir / "slurm_output.log",
        {
            1: [
                crab_line("12:00:03", "Starting experiment [1/1]: e1"),
                crab_line("12:00:03", "Started", ("e1", "Run 1")),
                crab_line("12:00:03", "Launched App 0  PID=77  nodes=['nodeA']", ctx),
                crab_line("12:00:04", "STDOUT Dump:", ctx, source="APP"),
                "[nodeA:1] *** benchmark output line that must not be echoed",
            ],
            2: [
                crab_line("12:00:08", "FINISHED  exit=0", ctx),
                crab_line("12:00:09", "Data saved to /home/user/d/e1", ("e1",)),
            ],
            3: [
                crab_line("12:00:10", "All experiments finished"),
                crab_line("12:00:10", "Engine run finished  elapsed=0:00:35"),
            ],
        },
    )
    lab.put_files(runs=[lab.run_row(1, "4240", data_dir)])
    result = lab.follow("4240")
    assert result.returncode == 0, result.stdout + result.stderr
    texts = stamped(result.stderr)
    assert all(re.match(r"^\[12:00:00\] ", line) for line in result.stderr.splitlines())  # NPLB_NOW
    wanted = [
        "Worker started",
        "Allocated 2 node(s)",
        "Starting experiment [1/1]: e1",
        "e1 Run 1: Started",
        "e1 Run 1 App 0: Launched App 0 PID=77",
        "e1 Run 1 App 0: FINISHED exit=0",
        "Data saved to e1",
        "All experiments finished",
        "Engine run finished elapsed=0:00:35",
    ]
    at = [next(i for i, t in enumerate(texts) if t.startswith(w)) for w in wanted]
    assert at == sorted(at)
    assert not any("benchmark output" in t or "STDOUT" in t for t in texts)
    assert any("ended: OK" in t for t in texts)
    name, report = report_of(result)
    assert report[0].startswith("VERDICT: OK row 1 job 4240 ")
    assert "[" not in result.stdout.split("\n", 1)[0].replace(
        "#report", ""
    )  # no narration on stdout
    assert not any(re.match(r"^\[\d\d:\d\d:\d\d\]", line) for line in result.stdout.splitlines())


def test_loop_with_a_job_id_polls_the_queue_then_ends_ok(lab: Lab) -> None:
    data_dir = lab.data_root / "run-b"
    write_log(data_dir, synth_log(["e1"], 2, tail=False)[:3])
    lab.put_files(runs=[lab.run_row(1, "4242", data_dir)])
    lab.states("PENDING", "RUNNING", "RUNNING", "GONE")
    lab.grow(data_dir / "slurm_output.log", {3: synth_log(["e1"], 2)[3:]})
    result = lab.follow("4242")
    assert result.returncode == 0, result.stdout + result.stderr
    texts = stamped(result.stderr)
    assert texts[0].startswith("following job 4242")
    squeue_calls = [c for c in lab.calls() if c.startswith("squeue")]
    assert squeue_calls[0] == "squeue -h -j 4242 -o %T %M"
    assert lab.forbidden() == []
    _, report = report_of(result)
    assert report[0].startswith(
        "VERDICT: OK row 1 job 4242 t1 split-i a2a nplb0_ar0_sr0: 2 apps ok"
    )


def test_queued_job_without_a_log_says_so(lab: Lab) -> None:
    data_dir = lab.data_root / "run-c"
    data_dir.mkdir()  # submitted, not started: the folder exists, the log does not
    lab.put_files(runs=[lab.run_row(1, "4243", data_dir)])
    lab.states("PENDING", "PENDING", "PENDING", "RUNNING", "GONE")
    lab.grow(data_dir / "slurm_output.log", {4: synth_log(["e1"], 1)})
    result = lab.follow("4243", FOLLOW_POLL="0.1")
    assert result.returncode == 0, result.stdout + result.stderr
    texts = stamped(result.stderr)
    queued = [t for t in texts if t == "job queued (PENDING), no log yet"]
    assert len(queued) == 1  # once per state change, not every poll
    assert texts.index(queued[0]) < texts.index("Worker started")
    assert "job 4243 RUNNING" in texts[texts.index(queued[0]) + 1 : texts.index("Worker started")]


def test_heartbeat_when_nothing_new_arrives(lab: Lab) -> None:
    data_dir = lab.data_root / "run-e"
    write_log(data_dir, synth_log(["e1"], 1, tail=False))
    lab.put_files(runs=[lab.run_row(1, "4245", data_dir)])
    lab.states(*(["RUNNING|1:45"] * 20), "GONE")
    lab.grow(data_dir / "slurm_output.log", {21: synth_log(["e1"], 1)[-2:]})
    result = lab.follow("4245", FOLLOW_POLL="0.1", FOLLOW_HEARTBEAT="0.5")
    assert result.returncode == 0, result.stdout + result.stderr
    beats = [t for t in stamped(result.stderr) if t.startswith("job 4245 RUNNING ")]
    assert beats, result.stderr
    assert all(re.fullmatch(r"job 4245 RUNNING 01:45, last log line \d+ s ago", b) for b in beats)
    assert 2 <= len(beats) <= 6  # one per heartbeat interval, not one per poll


def test_progress_counts_from_the_block_config(lab: Lab) -> None:
    # 1. the run's own config.json in the data dir
    data_dir = lab.data_root / "run-f"
    config = {
        "experiments": {"e1": {"apps": {"0": {}, "1": {}}}, "e2": {"apps": {"0": {}, "1": {}}}}
    }
    lines = synth_log(["e1", "e2"], 2, failed={("e2", 1)})
    write_log(data_dir, lines)
    (data_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    lab.put_files(runs=[lab.run_row(1, "4246", data_dir)])
    lab.states("GONE")
    result = lab.follow("4246")
    assert result.returncode == 0, result.stdout + result.stderr
    texts = stamped(result.stderr)
    ends = [t for t in texts if "FINISHED" in t or "FAILED" in t]
    assert [t.rsplit("(", 1)[1] for t in ends] == [
        "apps 1/4)",
        "apps 2/4)",
        "apps 3/4)",
        "apps 4/4)",
    ]
    _, report = report_of(result)
    table = {
        row.split()[0]: row.split() for row in report if re.match(r"^\s+e[12]\s+\d+/\d+\s", row)
    }
    assert table["e1"][1] == "2/2" and table["e2"][1] == "1/2"
    assert report[0].startswith("VERDICT: WARN row 1 job 4246 ")  # a failed app downgrades OK
    assert "1 failed" in report[0]
    assert any("e2" in row and "app 1" in row and "exit 17" in row for row in report)

    # 2. no config.json in the data dir: the schedule row's block file
    other = lab.data_root / "run-g"
    write_log(other, synth_log(["e1", "e2"], 2))
    (lab.root / "camp" / "t1_split-i_a2a.json").write_text(json.dumps(config), encoding="utf-8")
    lab.put_files(runs=[lab.run_row(1, "4247", other)])
    result = lab.follow("4247")
    texts = stamped(result.stderr)
    assert [t for t in texts if "FINISHED" in t][-1].endswith("(apps 4/4)")

    # 3. neither: no counts, and the table counts what the log shows
    bare = lab.data_root / "run-h"
    write_log(bare, synth_log(["e1"], 3))
    lab.put_files(runs=[lab.run_row(2, "4248", bare, victim="agtr")])
    result = lab.follow("4248")
    assert not any("apps " in t and "/" in t.rsplit("(", 1)[-1] for t in stamped(result.stderr))
    _, report = report_of(result)
    assert any(re.match(r"^\s+e1\s+3/3\s", row) for row in report)


def test_summary_page_and_run_log_update(lab: Lab) -> None:
    data_dir = lab.data_root / "run-i"
    write_log(data_dir, synth_log(["e1", "e2"], 2, failed=set()))
    (data_dir / "nplb_state.json").write_text(json.dumps({"label": B0}), encoding="utf-8")
    lab.put_files(runs=[lab.run_row(1, "4249", data_dir)])
    lab.states("GONE")
    result = lab.follow("4249")
    assert result.returncode == 0, result.stdout + result.stderr
    name, report = report_of(result)
    assert name == "run-1"
    assert report[0] == (
        "VERDICT: OK row 1 job 4249 t1 split-i a2a nplb0_ar0_sr0: 4 apps ok in 2m00s (est 5.0 min)"
    )
    text = "\n".join(report)
    assert re.search(r"^\s+e1\s+2/2\s+\S+", text, re.MULTILINE)
    assert f"data    {data_dir}" in report
    assert f"net     state {B0} (nplb_state.json in the data dir)" in report
    assert "next    nplb next" in report
    assert (
        max(len(x) for x in report if not x.startswith("data ")) <= 100
    )  # the pager cuts the path
    row = lab.runs_text().splitlines()[1].split("\t")
    assert row[8:10] == ["OK", "120"]  # Engine run finished elapsed=0:02:00
    assert "ended OK (job log: Engine run finished)" in row[10]
    assert list((lab.root / "work").glob("*.tmp")) == []

    # no Engine elapsed in the log: the time from the submit to the end as seen (11:00 -> 12:00)
    other = lab.data_root / "run-j"
    lines = synth_log(["e1"], 2, tail=False) + [crab_line("12:01:00", "Engine run finished")]
    write_log(other, lines)
    lab.put_files(runs=[lab.run_row(1, "4250", other)])
    result = lab.follow("4250")
    name, report = report_of(result)
    assert report[0].startswith(
        "VERDICT: OK row 1 job 4250 t1 split-i a2a nplb0_ar0_sr0: 2 apps ok in 60m00s"
    )
    assert any(x.startswith("net     no nplb_state.json in the data dir") for x in report)
    assert lab.runs_text().splitlines()[1].split("\t")[8:10] == ["OK", "3600"]

    # a failed attempt: the status, the time and the reason are written; the report says why first
    failed = lab.data_root / "run-k"
    write_log(failed, synth_log(["e1"], 2, tail=False))
    lab.put_files(runs=[lab.run_row(2, "4251", failed, victim="agtr")])
    lab.scontrol("JobState=TIMEOUT Reason=TimeLimit\n   RunTime=00:10:00 ExitCode=0:15\n")
    result = lab.follow("4251")
    assert result.returncode == 1, result.stdout + result.stderr
    name, report = report_of(result)
    assert name == "run-2"
    assert report[0].startswith("VERDICT: FAIL row 2 job 4251 ")
    assert "TIMEOUT (scontrol, exit 0:15)" in report[0]
    row = lab.runs_text().splitlines()[1].split("\t")
    assert row[8] == "FAIL" and row[9] == "3600" and "TIMEOUT" in row[10]

    # a bare job id (no run-log row) or a data dir: no row facts, nothing written to the run log
    lab.put_files(runs=[lab.run_row(1, "1", data_dir, status="OK", elapsed="9")])
    before = lab.runs_text()
    bare = lab.data_root / "run-l"
    write_log(bare, synth_log(["e1"], 2))
    result = lab.follow(str(bare))
    name, report = report_of(result)
    assert name == "run-run-l"
    assert report[0].startswith("VERDICT: OK run-l: 2 apps ok in 2m00s")
    assert " row " not in report[0] and "est" not in report[0]
    assert lab.runs_text() == before


def test_the_failing_a2a_fixture_report(lab: Lab) -> None:
    data_dir = fixture_run(lab.root, "a2a")
    lab.put_files(runs=[lab.run_row(1, "4300", data_dir, victim="a2a")])
    lab.states("GONE")
    result = lab.follow("4300")
    assert result.returncode == 0, result.stdout + result.stderr  # WARN is not a failure
    name, report = report_of(result)
    assert name == "run-1"
    assert report[0].startswith("VERDICT: WARN row 1 job 4300 t1 split-i a2a nplb0_ar0_sr0: ")
    assert "41 apps ok, 3 failed in 10m48s" in report[0]
    text = "\n".join(report)
    assert "ppn4_alone, ppn8_alone" in text
    assert re.search(r"^\s+ppn4_alone\s+10/11\s", text, re.MULTILINE)
    assert re.search(r"^\s+ppn8_alone\s+9/11\s", text, re.MULTILINE)
    assert re.search(r"ppn4_alone\s+app 10\s+exit 17\s+ib_md.c:296\s+UCX\s+ERROR ibv_reg_mr", text)
    row = lab.runs_text().splitlines()[1].split("\t")
    assert row[8:10] == ["WARN", "648"] and "ppn4_alone, ppn8_alone" in row[10]
    ag = fixture_run(lab.root / "ag", "ag")
    lab.put_files(runs=[lab.run_row(1, "4301", ag, victim="a2a")])
    result = lab.follow("4301")
    _, report = report_of(result)
    assert report[0].startswith("VERDICT: OK row 1 job 4301 ")
    assert "44 apps ok in 12m21s" in report[0]


def test_target_resolution(lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in lab.env().items():
        monkeypatch.setenv(key, value)
    resolve = follow.resolve_target
    with pytest.raises(follow.FollowError, match="nothing to follow"):
        resolve(None)

    # no run log: the newest folder under DATA_ROOT
    old, new = lab.data_root / "old", lab.data_root / "new"
    old.mkdir()
    new.mkdir()
    os.utime(old, (1_000_000, 1_000_000))
    (lab.data_root / "metadata.csv").write_text("x\n", encoding="utf-8")  # a file is not a run
    assert resolve(None).data_dir == new
    assert resolve(None).job is None

    # the run log: the newest SUBMITTED attempt, else the newest attempt
    d1, d2, d3 = lab.data_root / "d1", lab.data_root / "d2", lab.data_root / "d3"
    for d in (d1, d2, d3):
        d.mkdir()
    lab.put_files(
        runs=[
            lab.run_row(1, "101", d1, status="OK", elapsed="60"),
            lab.run_row(2, "102", d2, status="SUBMITTED", victim="agtr"),
            lab.run_row(1, "103", d3, status="FAIL", elapsed="5"),
        ]
    )
    target = resolve(None)
    assert (target.job, target.data_dir, target.run.n, target.row.n) == ("102", d2, 2, 2)
    lab.put_files(
        runs=[lab.run_row(1, "101", d1, status="OK"), lab.run_row(1, "103", d3, status="FAIL")]
    )
    assert resolve(None).job == "103"

    # a schedule row number: its newest attempt; a job id; a data dir path; a folder name
    assert resolve("1").job == "103"
    assert resolve("101").job == "101"  # a run-log job id wins over a row number
    assert resolve(str(d1)).job == "101" and resolve(str(d1)).run.n == 1
    assert resolve("d3").data_dir == d3
    with pytest.raises(follow.FollowError, match="row 2 has no attempt"):
        resolve("2")

    # a job id the run log does not know: scontrol says where its log goes
    lab.scontrol(f"JobState=RUNNING Reason=None\n   StdOut={d2}/slurm_output.log\n")
    bare = resolve("9999")
    assert (bare.job, bare.data_dir, bare.run) == ("9999", d2, None)
    (lab.fake / "scontrol.out").unlink()
    with pytest.raises(follow.FollowError) as err:
        resolve("9999")
    message = str(err.value)
    assert "9999" in message and "run log" in message and "scontrol" in message
    assert "data dir" in message
    with pytest.raises(follow.FollowError, match="nothing found"):
        resolve("no-such-folder")

    # through the CLI: nothing to follow is exit 2 and no report
    shutil.rmtree(lab.data_root)
    (lab.root / "work" / "campaign_runs.tsv").unlink()
    lab.data_root.mkdir()
    result = lab.follow()
    assert result.returncode == 2 and result.stdout == ""
    assert "nothing to follow" in result.stderr


def test_ctrl_c_leaves_the_job_alone(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = lab.data_root / "run-m"
    write_log(data_dir, synth_log(["e1"], 1, tail=False))
    lab.put_files(runs=[lab.run_row(1, "4260", data_dir)])
    lab.states("RUNNING")
    for key, value in lab.env().items():
        monkeypatch.setenv(key, value)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(follow, "_sleep", interrupt)
    before = lab.runs_text()
    code = follow.main(["follow", "4260"])
    captured = capsys.readouterr()
    assert code == 130
    assert captured.err.rstrip().endswith(
        "stopped following; the job keeps running (nplb follow to resume)"
    )
    assert captured.out == ""
    assert lab.runs_text() == before and "SUBMITTED" in before
    assert lab.forbidden() == []
    assert all(c.startswith(("squeue", "scontrol")) for c in lab.calls())


def test_follow_is_read_only(lab: Lab) -> None:
    data_dir = lab.data_root / "run-n"
    write_log(data_dir, synth_log(["e1"], 2, tail=False))
    lab.put_files(runs=[lab.run_row(1, "4261", data_dir)])
    lab.states("RUNNING", "RUNNING", "GONE")
    lab.grow(data_dir / "slurm_output.log", {3: synth_log(["e1"], 2)[-2:]})

    def tree() -> dict[str, tuple[int, int]]:
        found = {}
        for path in sorted(lab.root.rglob("*")):
            rel = path.relative_to(lab.root).as_posix()
            if rel.startswith(("fake", "bin", "work", "elsewhere")):
                continue
            stat = path.stat()
            found[rel] = (stat.st_size if path.is_file() else 0, int(stat.st_mtime))
        return found

    before = tree()
    result = lab.follow("4261")
    assert result.returncode == 0, result.stdout + result.stderr
    after = tree()
    # the log grew through the fake squeue; nothing else under the data tree changed
    assert after.keys() == before.keys()
    assert {k for k in after if after[k] != before[k]} <= {
        "data/nplb_h100_gpu/run-n/slurm_output.log",
        "data/nplb_h100_gpu/run-n",
    }
    assert lab.forbidden() == []
    assert {c.split()[0] for c in lab.calls()} <= {"squeue", "scontrol"}
    assert list((lab.root / "work").iterdir()) == [lab.root / "work" / "campaign_runs.tsv"]
    assert not (lab.root / "reports").exists()  # the python prints; the verb pages and saves


def test_follow_start_banner(lab: Lab) -> None:
    data_dir = lab.data_root / "run-o"
    write_log(data_dir, synth_log(["e1"], 1))
    lab.put_files(runs=[lab.run_row(1, "4262", data_dir)])
    lab.states("GONE")
    result = lab.follow("4262")
    first = result.stderr.splitlines()[0]
    assert first == (
        f"[{STAMP_NOW}] following job 4262: Ctrl-C only stops following, the job keeps running"
    )
    assert len(first) <= 100
    second = result.stderr.splitlines()[1]
    assert f"data dir {data_dir}" in second


def test_pages_fit_for_a_large_run(lab: Lab) -> None:
    sizes = {
        "small": (["e1", "e2", "e3"], 8, set()),
        "large": ([f"x{k:02d}" for k in range(11)], 8, set()),
    }
    sizes["large"] = (
        sizes["large"][0],
        8,
        {(f"x{k:02d}", app) for k in range(0, 11, 2) for app in (1, 5, 7)},
    )
    for label, (exps, apps, failed) in sizes.items():
        data_dir = lab.data_root / f"run-{label}"
        write_log(data_dir, synth_log(exps, apps, failed=failed))
        for exp, app in sorted(failed):
            (data_dir / exp).mkdir(exist_ok=True)
            (data_dir / exp / f"error_app_{app}.log").write_text(
                f"App {app} exit=17\n\n--- STDOUT ---\n[1.0] [nodeA:1:0] x.c:1 UCX ERROR failed "
                + "y" * 150
                + "\n",
                encoding="utf-8",
            )
        lab.put_files(runs=[lab.run_row(1, f"50{len(exps)}", data_dir)])
        lab.states("GONE")
        result = lab.follow(f"50{len(exps)}")
        name, report = report_of(result)
        assert (
            max(len(x) for x in report if not x.startswith("data ")) <= 100
        )  # the pager cuts the path
        paged = subprocess.run(
            [sys.executable, str(PAGES_PY), "emit", name],
            input="\n".join(report) + "\n",
            env=lab.env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert paged.returncode == 0, paged.stderr
        lines = paged.stdout.splitlines()
        assert re.match(r"run-1 \| page 1/\d+", lines[0])
        assert lines[1].startswith("VERDICT: ")
        assert len(lines) <= 38 and max(len(line) for line in lines) <= 100
    # the large run has 33 failed apps: the list is capped and says how many more
    assert any(re.match(r"^\s*\+\d+ more", line) for line in report)


# --- the verb --------------------------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    return chk


def test_dispatcher_follow_verb(lab: Lab, checkout: Path) -> None:
    data_dir = fixture_run(lab.root / "fx", "ag")
    result = subprocess.run(
        ["bash", str(checkout / "examples" / "nplb" / "nplb"), "follow", str(data_dir)],
        env=lab.env(),
        cwd=lab.root / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert re.match(rf"run-{AG_RUN} \| page 1/\d+", lines[0])
    assert lines[1].startswith(f"VERDICT: OK {AG_RUN}: 44 apps ok in 12m21s")
    assert len(lines) <= 38 and max(len(line) for line in lines) <= 100
    assert "#report" not in result.stdout
    assert stamped(result.stderr)[0].startswith("following ")
    saved = [p.name for p in (lab.root / "reports").iterdir() if p.name.endswith(".txt")]
    assert [name.split("_", 1)[1] for name in saved] == [f"run-{AG_RUN}.txt"]

    # the failing case keeps its exit code through the pipe to the pager
    lab.scontrol("JobState=FAILED\n   ExitCode=1:0\n")
    failed = lab.data_root / "run-q"
    write_log(failed, synth_log(["e1"], 1, tail=False))
    lab.put_files(runs=[lab.run_row(3, "4270", failed)])
    bad = subprocess.run(
        ["bash", str(checkout / "examples" / "nplb" / "nplb"), "follow", "4270"],
        env=lab.env(),
        cwd=lab.root / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert bad.returncode == 1
    assert bad.stdout.splitlines()[0].startswith("run-3 | page 1/")
    assert bad.stdout.splitlines()[1].startswith("VERDICT: FAIL row 3 ")

    # an unknown target: exit 2, the message on stderr, no page
    unknown = subprocess.run(
        ["bash", str(checkout / "examples" / "nplb" / "nplb"), "follow", "no-such-thing"],
        env=lab.env(),
        cwd=lab.root / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert unknown.returncode == 2 and unknown.stdout == ""
    assert "nothing found" in unknown.stderr

    helped = subprocess.run(
        ["bash", str(checkout / "examples" / "nplb" / "nplb"), "help"],
        env=lab.env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    help_lines = helped.stdout.splitlines()
    assert any(line.startswith("  follow") for line in help_lines)
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100


def test_python_310_safe(lab: Lab) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    data_dir = fixture_run(lab.root / "fx", "a2a")
    result = subprocess.run(
        [python310, str(FOLLOW_PY), "follow", str(data_dir)],
        env=lab.env(),
        cwd=lab.root / "elsewhere",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _, report = report_of(result)
    assert report[0].startswith(f"VERDICT: WARN {A2A_RUN}: ")


def test_a_forgotten_job_or_a_missing_squeue_leaves_the_log_to_decide(lab: Lab) -> None:
    data_dir = lab.data_root / "run-r"
    write_log(data_dir, synth_log(["e1"], 1))
    lab.put_files(runs=[lab.run_row(1, "4280", data_dir)])
    lab.states("ERR")  # "Invalid job id specified": the job is over and forgotten
    result = lab.follow("4280")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(t.startswith("queue:") for t in stamped(result.stderr))
    assert "job 4280 is not in the queue" in stamped(result.stderr)

    missing = lab.follow("4280", SQUEUE=str(lab.bin / "no-such-squeue"))
    assert missing.returncode == 0, missing.stdout + missing.stderr
    said = [t for t in stamped(missing.stderr) if t.startswith("queue:")]
    assert len(said) == 1 and "not found" in said[0]


def test_a_real_sigint_stops_only_the_following(lab: Lab) -> None:
    """The signal goes to the follower this test started, never to anything else."""
    data_dir = lab.data_root / "run-s"
    write_log(data_dir, synth_log(["e1"], 1, tail=False))
    lab.put_files(runs=[lab.run_row(1, "4290", data_dir)])
    lab.states("RUNNING")  # the job never ends
    proc = subprocess.Popen(
        [sys.executable, str(FOLLOW_PY), "follow", "4290"],
        env=lab.env(FOLLOW_POLL="0.05"),
        cwd=lab.root / "elsewhere",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stderr is not None
        assert "Ctrl-C only stops following" in proc.stderr.readline()
        for _ in range(100):  # wait until it has polled the queue at least once
            if any(c.startswith("squeue") for c in lab.calls()):
                break
            time.sleep(0.05)
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 130, out + err
    assert err.rstrip().endswith("stopped following; the job keeps running (nplb follow to resume)")
    assert out == ""
    assert "SUBMITTED" in lab.runs_text() and lab.forbidden() == []
