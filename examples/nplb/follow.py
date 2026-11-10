#!/usr/bin/env python3
"""`nplb follow`: follow a submitted CRAB job on the lab, narrate it, say how it ended, summarise it.

    follow.py follow [TARGET]      TARGET: a schedule row number, a Slurm job id, a data dir path or the
                                   name of a folder under $DATA_ROOT; none = the newest SUBMITTED
                                   attempt of the run log, else the newest attempt, else the newest
                                   folder under $DATA_ROOT

Read only: it submits and cancels nothing and calls nothing but `squeue -h -j JOB -o "%T %M"` and
`scontrol show job JOB` (env SQUEUE, SCONTROL name other commands). Ctrl-C only stops following.
Narration goes to stderr, one `[HH:MM:SS] text` line per worker message that matters, a line when the
queue state changes and a heartbeat when nothing new arrived. Stdout gets `#report NAME` and then the
report, verdict first; the verb pages and saves it as NAME. When the target is a row of campaign_runs.tsv
(in $WORK, see run_log.py) its status, elapsed_s and note are updated at the end.

The lab cluster has no Slurm accounting: `sacct` answers nothing and `crab status` says UNKNOWN for
every finished job, so this does not ask `crab`. What marks the end of a run is the last non-empty line of
`<data_dir>/slurm_output.log` (ANSI colour codes stripped): `Engine run finished` is a success;
`Worker fatal error`, any `FATAL`, `Worker terminated (SIGTERM)` and `Worker interrupted by user` are
failures; `All experiments finished` is neither (it is printed after failed experiments too); a SIGKILL
(walltime, out of memory) leaves no final line. `decide_outcome` ports `job_outcome` of gpu_lab_run.sh and
keeps its order: scontrol (a final bad state decides; COMPLETED 0:0 lets the log decide; a live state
resets the silence clocks), then the last log line, then silence for $FOLLOW_STALL seconds. An OK is a
WARN when metadata.csv in the data root lists a FAILED experiment of this run.

Env: FOLLOW_POLL (seconds between polls, 10), FOLLOW_STALL (silence that ends a job without a final
line, 120), FOLLOW_HEARTBEAT (quiet seconds before a heartbeat line, 15), DATA_ROOT (default
<checkout>/data/nplb_h100_gpu), WORK, CAMPAIGN_DIR, NPLB_NOW (fixes the clock, for tests).
Exit 0 for OK and WARN, 1 for FAIL, 2 when the target or an input file is wrong (message on stderr),
130 on Ctrl-C. Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402
import run_log  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
OK_MARKERS = re.compile(r"Engine run finished")
FAIL_MARKERS = re.compile(r"FATAL|Worker fatal error|Worker terminated|Worker interrupted")
STOP_MARKERS = re.compile(r"Worker terminated|Worker interrupted")  # a stop from outside
# JobState values of `scontrol show job` that are final and bad (COMPLETED needs the log as well).
BAD_STATES = re.compile(
    r"FAILED|TIMEOUT|CANCELLED.*|NODE_FAIL|OUT_OF_MEMORY|BOOT_FAIL|DEADLINE|PREEMPTED"
)
QUEUED_STATES = ("PENDING", "CONFIGURING", "REQUEUE", "SUSPENDED", "STOPPED", "RESV_DEL_HOLD")
RUNNING_STATES = ("RUNNING", "COMPLETING", "RESIZING", "SIGNALING", "STAGE_OUT")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LINE = re.compile(r"^\[(\d+):(\d+):(\d+)\]\s+([A-Z]+)\s+(\S+)(.*)$")
ELAPSED = re.compile(r"Engine run finished\s+elapsed=(\d+):(\d+):(\d+)")
STARTING = re.compile(r"Starting experiment \[\d+/\d+\]:\s*(\S+)")
APP_EVENT = re.compile(r"^(Launched|FINISHED|FAILED)\b(.*)$")
EXIT_CODE = re.compile(r"exit=(-?\d+)")
# The worker messages that are narrated; the benchmark's own output (source APP) never is.
SHOWN = re.compile(
    r"^(Worker started|Allocated \d+ node|Starting experiment|Experiment\b.*\bfailed|Started$"
    r"|Launched App \d+|FINISHED\b|FAILED\b|HARD TIMEOUT|Converged at run|Data saved to"
    r"|All experiments finished|Engine run finished)|HARD TIMEOUT"
)
LEVELS_SHOWN = ("WARN", "ERROR", "FATAL", "CRITICAL")
ERROR_WORDS = re.compile(r"error|fatal|fail|exception|traceback", re.IGNORECASE)
ERROR_HEADER = re.compile(r"^(App \d+ exit=|--- (STDOUT|STDERR) ---)")
ERROR_PREFIX = re.compile(r"^\[\d+\.\d+\]\s+\[[^\]]*\]\s+")
TAIL_BYTES = 8192
PAGE_COLS = 100
MESSAGE_WIDTH = 78
BRIEF_WIDTH = 70
NAMES_WIDTH = 70
CATCH_UP = 40  # narrated messages of a log that already has lines when we start
MAX_FAILED_APPS = 5
MAX_EXPERIMENT_ROWS = 16
STOPPED = "stopped following; the job keeps running (nplb follow to resume)"


class FollowError(Exception):
    """A target that cannot be followed or a bad setting: the message goes to stderr, exit 2."""


def cut_text(text: str, width: int) -> str:
    """TEXT, or its first WIDTH-1 characters and a "~" when longer."""
    return text if len(text) <= width else text[: width - 1] + "~"


# --- one worker log line ----------------------------------------------------------------------------


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def last_line(text: str) -> str:
    """The last non-empty line of TEXT ("" when there is none)."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.rstrip()
    return ""


def read_tail(path: Path, size: int = TAIL_BYTES) -> str:
    """The last SIZE bytes of a file as text ("" when it cannot be read)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - size))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def classify_last_line(line: str) -> str:
    """ "ok" (Engine run finished), "fail", "terminated" (a stop from outside) or "none"."""
    if FAIL_MARKERS.search(line):
        return "terminated" if STOP_MARKERS.search(line) else "fail"
    if OK_MARKERS.search(line):
        return "ok"
    return "none"


@dataclass(frozen=True)
class Parsed:
    """A timestamped log line: `[HH:MM:SS] LEVEL  SOURCE │ ctx │ ctx  message`."""

    stamp: int  # seconds since midnight
    level: str
    source: str  # CRAB (the engine) or APP (the benchmark's output)
    ctx: tuple[str, ...]  # experiment, "Run k", "App n": as many as the line has
    msg: str


def split_line(line: str) -> Parsed | None:
    """The parts of a worker log line, or None for a line without a timestamp (benchmark noise)."""
    found = LINE.match(strip_ansi(line).rstrip())
    if not found:
        return None
    hours, minutes, seconds, level, source, rest = found.groups()
    stamp = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    rest = rest.strip()
    if not rest.startswith("│"):
        return Parsed(stamp, level, source, (), rest)
    parts = [part.strip() for part in rest.split("│")[1:]]
    tail = re.split(r"\s{2,}", parts[-1], maxsplit=1)
    return Parsed(stamp, level, source, (*parts[:-1], tail[0]), tail[1] if len(tail) > 1 else "")


def filter_worker_line(line: str) -> str | None:
    """The narration text of a worker log line, or None when it is not shown.

    Shown: the engine's own progress messages and any WARN/ERROR/FATAL line of the engine. Never
    shown: the benchmark's output (source APP, and the raw lines it dumps without a timestamp).
    """
    parsed = split_line(line)
    if parsed is None or parsed.source != "CRAB":
        return None
    msg = " ".join(parsed.msg.split())
    known = SHOWN.search(msg) is not None
    if not known and parsed.level not in LEVELS_SHOWN and "FATAL" not in msg:
        return None
    if msg.startswith("Data saved to "):
        return "Data saved to " + msg.rsplit("/", 1)[-1]
    if msg.startswith("Launched App"):
        msg = msg.split(" nodes=")[0]
    elif not known and parsed.level in LEVELS_SHOWN:
        msg = f"{parsed.level} {msg}"
    text = f"{' '.join(parsed.ctx)}: {msg}" if parsed.ctx else msg
    return cut_text(text, MESSAGE_WIDTH)


def brief_line(line: str) -> str:
    """The message of a worker log line without time, level and source, cut to 70 columns."""
    parsed = split_line(line)
    text = " ".join([*parsed.ctx, parsed.msg]) if parsed else line
    return cut_text(" ".join(text.split()), BRIEF_WIDTH)


def span(first: int, last: int) -> int:
    """Seconds from FIRST to LAST (both seconds since midnight): +24 h when the clock wrapped."""
    delta = last - first
    return delta + 86400 if delta < 0 else delta


# --- what the log says so far ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppEvent:
    exp: str
    run: str
    app: str
    kind: str  # Launched, FINISHED or FAILED
    code: str  # the exit code of a FINISHED/FAILED line, "" otherwise


class RunBook:
    """Everything the worker log has told so far, one line at a time through `feed`.

    `apps` maps (experiment, app) to "running", "ok" or "failed" (failed is sticky over several
    runs); `stamps` maps an experiment to the first and last time the log mentions it.
    """

    def __init__(self) -> None:
        self.apps: dict[tuple[str, str], str] = {}
        self.codes: dict[tuple[str, str], str] = {}
        self.experiments: list[str] = []
        self.stamps: dict[str, list[int]] = {}
        self.first_stamp: int | None = None
        self.last_stamp: int | None = None
        self.elapsed_s: int | None = None

    def _touch(self, exp: str, stamp: int) -> None:
        if exp not in self.stamps:
            self.experiments.append(exp)
            self.stamps[exp] = [stamp, stamp]
        self.stamps[exp][1] = stamp

    def feed(self, line: str) -> AppEvent | None:
        """Take one log line in; returns the app event it holds, if any."""
        parsed = split_line(line)
        if parsed is None:
            return None
        if self.first_stamp is None:
            self.first_stamp = parsed.stamp
        self.last_stamp = parsed.stamp
        if parsed.source != "CRAB":
            return None
        starting = STARTING.search(parsed.msg)
        if starting:
            self._touch(starting.group(1), parsed.stamp)
        elif parsed.ctx:
            self._touch(parsed.ctx[0], parsed.stamp)
        done = ELAPSED.search(parsed.msg)
        if done:
            hours, minutes, seconds = (int(x) for x in done.groups())
            self.elapsed_s = hours * 3600 + minutes * 60 + seconds
        if len(parsed.ctx) != 3 or not parsed.ctx[2].startswith("App "):
            return None
        event = APP_EVENT.match(parsed.msg)
        if event is None:
            return None
        exp, run, app = parsed.ctx[0], parsed.ctx[1].removeprefix("Run "), parsed.ctx[2][4:]
        kind = event.group(1)
        code = EXIT_CODE.search(event.group(2))
        key = (exp, app)
        if kind == "Launched":
            self.apps.setdefault(key, "running")
        elif kind == "FAILED":
            self.apps[key] = "failed"
            self.codes[key] = code.group(1) if code else "?"
        elif self.apps.get(key) != "failed":
            self.apps[key] = "ok"
        return AppEvent(exp, run, app, kind, code.group(1) if code else "")

    def count(self, exp: str | None, state: str) -> int:
        return sum(1 for (e, _), s in self.apps.items() if s == state and exp in (None, e))

    @property
    def done(self) -> int:
        """Apps that ended, well or not."""
        return sum(1 for s in self.apps.values() if s != "running")


# --- metadata.csv, scontrol, the outcome -----------------------------------------------------------------


def metadata_failures(data_dir: Path) -> tuple[str, list[str]]:
    """("missing" | "unreadable" | "clean" | "failed", names).

    Reads metadata.csv in the data root (the parent of DATA_DIR) and names the experiments of this
    job that it lists as FAILED or with failed runs.
    """
    path = Path(data_dir).parent / "metadata.csv"
    job = Path(data_dir).name
    try:
        handle = path.open(encoding="utf-8", errors="replace", newline="")
    except OSError:
        return "missing", []
    failed: list[str] = []
    try:
        with handle:
            for row in csv.DictReader(handle):
                rel = (row.get("relative_path") or "").strip()
                if not (rel.startswith(f"./{job}/") or rel.startswith(f"{job}/")):
                    continue
                try:
                    runs = int(float(row.get("failed_runs") or 0))
                except ValueError:
                    runs = 0
                name = row.get("experiment_name") or rel.rsplit("/", 1)[-1]
                if (row.get("status") or "").strip().upper() == "FAILED" or runs > 0:
                    if name not in failed:
                        failed.append(name)
    except (csv.Error, OSError):
        return "unreadable", []
    return ("failed", failed) if failed else ("clean", [])


def parse_scontrol(text: str) -> tuple[str, str]:
    """(JobState, ExitCode) of `scontrol show job` output; empty strings when absent."""
    state = re.search(r"JobState=([A-Z_]+)", text)
    code = re.search(r"ExitCode=(\d+:\d+)", text)
    return (state.group(1) if state else "", code.group(1) if code else "")


@dataclass(frozen=True)
class Outcome:
    """How a job ended: verdict OK, WARN, FAIL or UNDECIDED (keep polling)."""

    verdict: str
    why: str = ""  # the short reason
    src: str = ""  # where the verdict comes from: log or scontrol
    note: str = ""  # the detail: last log line, error tail, the failed experiments
    side: str = ""  # a check that could not be made (metadata.csv missing)
    terminated: bool = False  # a stop from outside (SIGTERM, interrupted)

    def reason(self) -> str:
        return f"{self.why}; {self.note}" if self.note else self.why


@dataclass
class Clocks:
    """The silence clocks kept across polls (seconds on the caller's monotonic clock)."""

    gone_at: float | None = None  # when the job was first seen outside the queue
    log_sig: int | None = None  # size of slurm_output.log at the last poll
    log_sig_at: float | None = None  # when that size last changed


def error_tail(data_dir: Path) -> str:
    """The last non-empty line of slurm_error.log (the end of a traceback), cut; "" when none."""
    text = read_tail(Path(data_dir) / "slurm_error.log", 1024)
    return cut_text(last_line("\n".join(text.splitlines()[-5:])), 50)


def _ok(data_dir: Path, why: str) -> Outcome:
    """The job ended well; the metadata.csv cross-check then downgrades it or notes a gap."""
    state, names = metadata_failures(data_dir)
    if state == "failed":
        note = "metadata.csv lists failed runs: " + cut_text(", ".join(names), NAMES_WIDTH)
        return Outcome("WARN", why, "log", note)
    side = {
        "missing": "metadata.csv not found next to the data dir: cross-check skipped",
        "unreadable": "metadata.csv could not be read: cross-check skipped",
    }.get(state, "")
    return Outcome("OK", why, "log", side=side)


def decide_outcome(
    data_dir: Path, sc: tuple[str, str], clocks: Clocks, now: float, stall: float
) -> Outcome:
    """How the job ended, asked when it is no longer in the queue. Order:

    1. scontrol (SC = JobState and ExitCode, empty when it does not know the job): a final bad
       state is FAIL at once; COMPLETED 0:0 only lets the log decide; a state that is not final
       means the job is alive and the silence clocks restart.
    2. the last non-empty line of slurm_output.log against the markers.
    3. silence: the log's size unchanged for STALL seconds with no final line, or no log at all that
       long since the job was first seen outside the queue.
    Returns UNDECIDED while the log may still grow. NOW is a monotonic time in seconds.
    """
    data_dir = Path(data_dir)
    state, code = sc
    if clocks.gone_at is None:
        clocks.gone_at = now
    if not state:
        pass
    elif state == "COMPLETED":
        if code and code != "0:0":
            return Outcome("FAIL", f"COMPLETED (scontrol, exit {code})", "scontrol")
    elif BAD_STATES.fullmatch(state):
        exit_text = f", exit {code}" if code else ""
        return Outcome("FAIL", f"{state} (scontrol{exit_text})", "scontrol")
    else:
        clocks.gone_at = now
        clocks.log_sig_at = now
    errtail = error_tail(data_dir)
    tail = f"; slurm_error.log: {errtail}" if errtail else ""
    log = data_dir / "slurm_output.log"
    if log.is_file():
        size = log.stat().st_size
        if size != clocks.log_sig:
            clocks.log_sig = size
            clocks.log_sig_at = now
        line = last_line(strip_ansi(read_tail(log)))
        kind = classify_last_line(line)
        if kind in ("fail", "terminated"):
            why = "terminated (job log)" if kind == "terminated" else "FAILED (job log)"
            note = f"last log line: {brief_line(line)}"
            return Outcome("FAIL", why, "log", note, terminated=kind == "terminated")
        if kind == "ok":
            return _ok(data_dir, f"ended OK (job log: {OK_MARKERS.pattern})")
        if clocks.log_sig_at is not None and now - clocks.log_sig_at >= stall:
            note = f"log silent for {stall:g}s{tail}"
            return Outcome("FAIL", "ended without a final line (stopped early?)", "log", note)
    elif now - clocks.gone_at >= stall:
        note = f"no log for {stall:g}s{tail}"
        return Outcome("FAIL", "job left the queue without writing a log", "log", note)
    return Outcome("UNDECIDED")


# --- the report ------------------------------------------------------------------------------------------


def fmt_dur(seconds: int | None) -> str:
    """0m35s, 10m48s, 60m00s; "?" when unknown."""
    return "?" if seconds is None else f"{seconds // 60}m{seconds % 60:02d}s"


def first_error_line(data_dir: Path, exp: str, app: str) -> str:
    """The first line of error_app_<app>.log that reads as an error, without its time and host
    prefix; "" when there is no such log."""
    path = Path(data_dir) / exp / f"error_app_{app}.log"
    try:
        with path.open("rb") as handle:
            text = handle.read(TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [
        x.strip() for x in text.splitlines() if x.strip() and not ERROR_HEADER.match(x.strip())
    ]
    pick = next((x for x in lines if ERROR_WORDS.search(x)), lines[0] if lines else "")
    return " ".join(ERROR_PREFIX.sub("", pick).split())


def state_label(data_dir: Path) -> str:
    """The report line about nplb_state.json, the network state `nplb net record` wrote."""
    path = Path(data_dir) / net_state.RECORD_FILE
    if not path.is_file():
        return "no nplb_state.json in the data dir (nplb net record DIR)"
    try:
        label = json.loads(path.read_text(encoding="utf-8")).get("label")
    except (OSError, ValueError, AttributeError):
        return "nplb_state.json in the data dir cannot be read"
    return f"state {label} (nplb_state.json in the data dir)"


def build_report(
    outcome: Outcome,
    book: RunBook,
    data_dir: Path,
    config_apps: dict[str, int] | None,
    label: str,
    est_min: float | None,
    elapsed_s: int | None,
) -> tuple[str, list[str]]:
    """(verdict, the report lines): the verdict line first, then why, the experiments, the failed
    apps with their first error line, and where things are. A run whose log says an app failed is a
    WARN at best, even when metadata.csv does not say so."""
    ok, failed = book.count(None, "ok"), book.count(None, "failed")
    verdict = outcome.verdict
    by_apps = verdict == "OK" and failed > 0
    if by_apps:
        verdict = "WARN"
    apps = f"{ok} apps ok" + (f", {failed} failed" if failed else "")
    head = f"VERDICT: {verdict} {label}: "
    if verdict == "FAIL":
        body = f"{outcome.why}" + (f"; {apps}" if book.apps else "")
    else:
        body = f"{apps} in {fmt_dur(elapsed_s)}"
    est = f" (est {est_min:.1f} min)" if est_min else ""
    lines = [head + body + est]
    if len(lines[0]) > PAGE_COLS:  # too wide: the estimate moves to its own line
        lines = [cut_text(head + body, PAGE_COLS), f"est     {est_min:.1f} min"]
    lines.append(f"why     {outcome.why}")
    if outcome.note:
        lines.append(f"note    {cut_text(outcome.note, PAGE_COLS - 8)}")
    if by_apps:
        lines.append(f"note    {failed} apps failed (the log says so, metadata.csv does not)")
    if outcome.side:
        lines.append(f"check   {outcome.side}")
    names = list(config_apps or [])
    names += [name for name in book.experiments if name not in names]
    if names:
        width = max(12, *(len(name) for name in names))
        lines += ["", f"  {'experiment':<{width}}  apps ok  wall"]
    for name in names[:MAX_EXPERIMENT_ROWS]:
        seen = sum(1 for (exp, _) in book.apps if exp == name)
        total = config_apps[name] if config_apps and name in config_apps else seen
        stamps = book.stamps.get(name)
        wall = fmt_dur(span(*stamps)) if stamps else "-"
        bad = book.count(name, "failed")
        ratio = f"{book.count(name, 'ok')}/{total}"
        row = f"  {name:<{width}}  {ratio:<8} {wall}"
        lines.append(row + (f"  {bad} failed" if bad else ""))
    if len(names) > MAX_EXPERIMENT_ROWS:
        lines.append(f"  +{len(names) - MAX_EXPERIMENT_ROWS} more experiments")
    bad_apps = sorted(
        (key for key, state in book.apps.items() if state == "failed"),
        key=lambda k: (
            names.index(k[0]) if k[0] in names else 0,
            int(k[1]) if k[1].isdigit() else 0,
        ),
    )
    if bad_apps:
        lines += ["", "failed apps (first error line of error_app_N.log):"]
    for exp, app in bad_apps[:MAX_FAILED_APPS]:
        head2 = f"  {exp}  app {app}  exit {book.codes.get((exp, app), '?')}  "
        lines.append(
            cut_text(head2 + (first_error_line(data_dir, exp, app) or "(no error log)"), PAGE_COLS)
        )
    if len(bad_apps) > MAX_FAILED_APPS:
        lines.append(f"  +{len(bad_apps) - MAX_FAILED_APPS} more")
    lines += ["", f"data    {data_dir}", f"net     {state_label(data_dir)}", "next    nplb next"]
    return verdict, lines


def config_app_counts(*paths: Path) -> dict[str, int] | None:
    """{experiment: number of apps} from the first readable CRAB config of PATHS, else None."""
    for path in paths:
        try:
            experiments = json.loads(path.read_text(encoding="utf-8"))["experiments"]
            return {str(name): len(exp["apps"]) for name, exp in experiments.items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


# --- the target -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    poll: float
    stall: float
    heartbeat: float
    squeue: str
    scontrol: str
    data_root: Path

    @staticmethod
    def from_env() -> Config:
        def seconds(name: str, default: float) -> float:
            raw = os.environ.get(name, "")
            try:
                return float(raw) if raw else default
            except ValueError:
                raise FollowError(f"{name}={raw!r} is not a number of seconds") from None

        root = os.environ.get("DATA_ROOT") or str(CHECKOUT / "data" / "nplb_h100_gpu")
        return Config(
            seconds("FOLLOW_POLL", 10.0),
            seconds("FOLLOW_STALL", 120.0),
            seconds("FOLLOW_HEARTBEAT", 15.0),
            os.environ.get("SQUEUE") or "squeue",
            os.environ.get("SCONTROL") or "scontrol",
            Path(root),
        )


@dataclass(frozen=True)
class Target:
    """What to follow: the data dir, the Slurm job when known, the run-log attempt and schedule row."""

    data_dir: Path
    job: str | None
    run: run_log.Run | None
    row: run_log.Row | None
    name: str  # the report name


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


def _read_runs() -> list[run_log.Run]:
    try:
        return run_log.read_runs(run_log.runs_path())
    except run_log.LogError as exc:
        raise FollowError(str(exc)) from None


def _read_schedule() -> list[run_log.Row]:
    path = run_log.schedule_path()
    try:
        return run_log.read_schedule(path) if path.is_file() else []
    except run_log.LogError:
        return []  # follow does not need the schedule: only the row facts are lost


def _from_run(run: run_log.Run, schedule: list[run_log.Row]) -> Target:
    row = next((r for r in schedule if r.n == run.n), None)
    return Target(Path(run.data_dir), run.job, run, row, f"run-{run.n}")


def _bare(data_dir: Path, job: str | None, runs: list[run_log.Run], schedule: list) -> Target:
    """A target given as a job id or a data dir: its run-log attempt when the log knows it."""
    here = os.path.realpath(data_dir)
    known = [r for r in runs if (job and r.job == job) or os.path.realpath(r.data_dir) == here]
    if known:
        return _from_run(known[-1], schedule)
    return Target(data_dir, job, None, None, f"run-{_safe(job or data_dir.name)}")


def scontrol_text(cfg: Config, job: str) -> str:
    """The output of `scontrol show job JOB`, "" when scontrol is missing or does not know it."""
    try:
        done = subprocess.run(
            [cfg.scontrol, "show", "job", job],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def resolve_target(arg: str | None, cfg: Config | None = None) -> Target:
    """The run to follow; raises FollowError (exit 2) naming what was tried."""
    cfg = cfg or Config.from_env()
    runs, schedule = _read_runs(), _read_schedule()
    live = [r for r in runs if r.status != "CANCELLED"]
    if arg is None:
        submitted = [r for r in runs if r.status == "SUBMITTED"]
        if submitted or runs:
            return _from_run((submitted or runs)[-1], schedule)
        folders = (
            [p for p in cfg.data_root.iterdir() if p.is_dir()] if cfg.data_root.is_dir() else []
        )
        if not folders:
            raise FollowError(
                f"nothing to follow: the run log has no attempt and {cfg.data_root} has no data folder"
            )
        return _bare(max(folders, key=lambda p: p.stat().st_mtime), None, runs, schedule)
    tried = ["run log job id", "schedule row number", "scontrol job id", "data dir path"]
    if arg.isdigit():
        by_job = [r for r in runs if r.job == arg]
        if by_job:
            return _from_run(by_job[-1], schedule)
        if any(r.n == int(arg) for r in schedule):
            by_row = [r for r in live if r.n == int(arg)]
            if not by_row:
                raise FollowError(f"row {arg} has no attempt yet (run it: nplb run {arg})")
            return _from_run(by_row[-1], schedule)
        found = re.search(r"StdOut=(\S+)", scontrol_text(cfg, arg))
        if found:
            return _bare(Path(found.group(1)).parent, arg, runs, schedule)
    elif Path(arg).is_dir():
        return _bare(Path(arg).resolve(), None, runs, schedule)
    if (cfg.data_root / arg).is_dir():
        return _bare(cfg.data_root / arg, None, runs, schedule)
    tried.append(f"a folder under {cfg.data_root}")
    raise FollowError(f"nothing found for {arg!r}: tried {', '.join(tried)}")


# --- the loop -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Queue:
    state: str  # "" when the job is not listed (or squeue gave no answer)
    elapsed_s: int | None
    problem: str  # a squeue failure worth saying once, else ""


def parse_elapsed(text: str) -> int | None:
    """squeue's %M (1:45, 01:45:12, 1-02:03:04) in seconds."""
    found = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text)
    if not found:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def poll_queue(cfg: Config, job: str) -> Queue:
    """squeue's answer for the job: its state and elapsed time, or an empty state when it left."""
    cmd = [cfg.squeue, "-h", "-j", job, "-o", "%T %M"]
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return Queue("", None, f"{cfg.squeue} not found")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Queue("", None, f"{cfg.squeue} failed: {exc}")
    if done.returncode != 0:
        first = (done.stderr.strip().splitlines() or [""])[0]
        return Queue("", None, "" if "Invalid job id" in first else cut_text(first, 80))
    words = done.stdout.split()
    if not words:
        return Queue("", None, "")
    return Queue(words[0], parse_elapsed(words[1]) if len(words) > 1 else None, "")


def phase_of(state: str) -> str:
    """ "queued", "running" or "gone" (final or unknown: scontrol and the log decide)."""
    if state.startswith(QUEUED_STATES):
        return "queued"
    return "running" if state in RUNNING_STATES else "gone"


class Narrator:
    """`[HH:MM:SS] text` lines on stderr, at once. The stamp is the clock, or NPLB_NOW when set."""

    def __init__(self) -> None:
        self.last = time.monotonic()

    def say(self, text: str) -> None:
        print(f"[{net_state.now().strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)
        self.last = time.monotonic()


class LogReader:
    """Reads the new whole lines of a log that grows; a partial last line waits for its newline."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.partial = b""
        self.grown_at = time.monotonic()
        try:
            self.grown_at -= max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            pass
        self.started = False

    def read_new(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:  # truncated or replaced: start again
            self.offset, self.partial = 0, b""
        if size == self.offset:
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        self.offset += len(data)
        if self.started:
            self.grown_at = time.monotonic()
        self.started = True
        *lines, self.partial = (self.partial + data).split(b"\n")
        return [x.decode("utf-8", errors="replace").rstrip("\r") for x in lines]

    def flush(self) -> list[str]:
        """The unfinished last line, once the job is over."""
        rest, self.partial = self.partial, b""
        return [rest.decode("utf-8", errors="replace")] if rest.strip() else []


_sleep: Callable[[float], None] = time.sleep  # the loop's pause; tests replace it


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def follow_loop(
    target: Target, cfg: Config, narr: Narrator, config_apps: dict[str, int] | None
) -> tuple[Outcome, RunBook]:
    """Poll until the job's end is decided, narrating. Returns the outcome and what the log said."""
    job, log = target.job, target.data_dir / "slurm_output.log"
    book, clocks, reader = RunBook(), Clocks(), LogReader(log)
    total = sum(config_apps.values()) if config_apps else None
    started = time.monotonic()
    state, phase, problem, caught_up = None, "", "", False

    def take(lines: list[str]) -> None:
        nonlocal caught_up
        texts = []
        for line in lines:
            event = book.feed(line)
            text = filter_worker_line(line)
            if text and event and event.kind != "Launched" and total:
                text += f" (apps {book.done}/{total})"
            if text:
                texts.append(text)
        if lines and not caught_up:
            caught_up = True
            if len(texts) > CATCH_UP:
                narr.say(f"{len(texts) - CATCH_UP} earlier log messages not shown")
                texts = texts[-CATCH_UP:]
        for text in texts:
            narr.say(text)

    if job is None and not log.exists():
        narr.say(f"no log yet and no job id to ask the queue: waiting up to {cfg.stall:g}s")
    while True:
        now = time.monotonic()
        answer = poll_queue(cfg, job) if job else Queue("", None, "")
        if answer.problem and answer.problem != problem:
            narr.say(f"queue: {answer.problem}")
            problem = answer.problem
        new_phase = phase_of(answer.state) if job else "gone"
        if job and new_phase in ("queued", "running") and answer.state != state:
            queued_early = new_phase == "queued" and not log.exists()
            narr.say(
                f"job queued ({answer.state}), no log yet"
                if queued_early
                else f"job {job} {answer.state}"
            )
        elif job and new_phase == "gone" and phase != "gone":
            narr.say(f"job {job} left the queue" if phase else f"job {job} is not in the queue")
        state, phase = answer.state, new_phase
        take(reader.read_new())
        if phase != "gone":
            clocks.gone_at = None
        else:
            sc = parse_scontrol(scontrol_text(cfg, job)) if job else ("", "")
            outcome = decide_outcome(target.data_dir, sc, clocks, now, cfg.stall)
            if outcome.verdict != "UNDECIDED":
                take(reader.read_new() + reader.flush())
                return outcome, book
        if now - narr.last >= cfg.heartbeat:
            elapsed = answer.elapsed_s if answer.elapsed_s is not None else now - started
            what = (
                f"job {job} {answer.state if phase != 'gone' else 'left the queue'}"
                if job
                else "log"
            )
            ago = (
                f"last log line {int(max(0.0, now - reader.grown_at))} s ago"
                if log.exists()
                else "no log yet"
            )
            narr.say(f"{what} {_mmss(elapsed)}, {ago}")
        _sleep(cfg.poll)


def follow_target(target: Target, cfg: Config) -> int:
    """Follow TARGET, print the report on stdout, update the run log. The exit code of the verb."""
    narr = Narrator()
    which = f"job {target.job}" if target.job else target.data_dir.name
    narr.say(f"following {which}: Ctrl-C only stops following, the job keeps running")
    narr.say(f"data dir {target.data_dir}")
    config_apps = config_app_counts(
        target.data_dir / "config.json",
        run_log.schedule_path().parent / target.row.file if target.row else Path(os.devnull),
    )
    try:
        outcome, book = follow_loop(target, cfg, narr, config_apps)
    except KeyboardInterrupt:
        narr.say(STOPPED)
        return 130
    narr.say(
        f"{f'job {target.job}' if target.job else 'run'} ended: {outcome.verdict} ({outcome.src}) {outcome.why}"
    )
    end = net_state.now()
    elapsed = book.elapsed_s
    if elapsed is None and target.run is not None:
        submitted = datetime.strptime(target.run.submitted, net_state.ISO)
        elapsed = max(0, int((end - submitted).total_seconds()))
    if elapsed is None and book.first_stamp is not None and book.last_stamp is not None:
        elapsed = span(book.first_stamp, book.last_stamp)
    run, row = target.run, target.row
    if run is not None:
        label = f"row {run.n} job {run.job} {run.block} {run.layout} {run.victim} {run.state}"
    else:
        label = f"job {target.job}" if target.job else target.data_dir.name
    verdict, lines = build_report(
        outcome, book, target.data_dir, config_apps, label, row.est_min if row else None, elapsed
    )
    if run is not None and run.status != "CANCELLED":
        failed = book.count(None, "failed")
        reason = outcome.reason() + (
            f"; {failed} apps failed" if verdict != outcome.verdict else ""
        )
        try:
            run_log.update_run(
                run_log.runs_path(), run.job, status=verdict, elapsed_s=elapsed, note=reason
            )
        except (KeyError, ValueError, run_log.LogError, OSError) as exc:
            narr.say(f"run log not updated: {exc}")
    print(f"#report {target.name}")
    print("\n".join(lines), flush=True)
    return 1 if verdict == "FAIL" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Follow a submitted CRAB job on the lab.")
    sub = parser.add_subparsers(dest="verb", required=True)
    follow = sub.add_parser("follow", help="narrate the job, say how it ended, summarise it")
    follow.add_argument("target", nargs="?", help="row number, job id, data dir or folder name")
    args = parser.parse_args(argv)
    try:
        cfg = Config.from_env()
        return follow_target(resolve_target(args.target, cfg), cfg)
    except (FollowError, run_log.LogError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
