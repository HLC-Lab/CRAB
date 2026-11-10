#!/usr/bin/env python3
"""`nplb run`, `nplb cancel` and `nplb skip`: submit a campaign block, stop it, or drop rows from the plan.

    run_cmd.py run N|next [--force]       gates, then `crab run`, then the attempt goes into the run log
    run_cmd.py cancel                     scancel of the newest SUBMITTED job of the run log (asks first)
    run_cmd.py skip N... | --state LABEL | --tier T [--why TEXT] [--undo]

`run` evaluates seven gates and reports every failed one (`VERDICT: REFUSED row N: <first reason>`, then
one block per gate with the command to run next; exit 1, nothing submitted): (1) no attempt of any row is
SUBMITTED; (2) the row is in schedule.tsv and its block file exists; (3) the row is not DONE (rerun it with
`nplb run N --force`; `run next` never reruns); (4) the declared switch state ($WORK/net_state.json) is the
row's NPLB and AR; (5) `net_tools.sh record` on the nodes works and the NIC SR bit is the row's; (6) `crab
validate` accepts the block; (7) no job RUNNING or PENDING in the partition (nodes are exclusive; --force
waives it). Rows with state `-` (local rehearsal) skip 4 and 5. Then it runs `crab run -p $PRESET --json
BLOCK` from the checkout, copies the state record into the data dir as nplb_state.json, writes
schedule_row.json there, appends the attempt (SUBMITTED) to campaign_runs.tsv (see run_log.py) and prints
`submitted: row N job J -> DIR` and, as its last line, `job J`; the verb then follows the job. A failed
submit prints `VERDICT: FAIL submit` and logs nothing. `--no-follow` is the verb's flag, accepted here.

`cancel` shows the newest SUBMITTED attempt and asks `Cancel it? [y/N]` (a terminal, or NPLB_YES=1);
only `scancel JOB` for a job of our own run log runs, and the attempt becomes CANCELLED.
`skip` appends a SKIPPED attempt (job and data_dir `-`) for every selected row that is not DONE, RUNNING or
already SKIPPED, so `nplb next` stops proposing it; `--undo` turns those attempts into CANCELLED.

Env: NET_TOOLS (default net_tools.sh next to this file), CRAB (default <checkout>/.venv/bin/crab, else
crab), PRESET (nplb_h100_gpu), SQUEUE, SCANCEL, PARTITION (h100_4nodes), NPLB_YES, WORK, CAMPAIGN_DIR,
NPLB_NOW. Narration goes to stderr as `[HH:MM:SS] text`, reports to stdout. Exit 0, 1 when refused or
failed or declined, 2 on a bad command line or input file. Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402
import run_log  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
JOB_ID = re.compile(r"[0-9]+([_+][0-9]+)*")  # Slurm job, array and het ids: never a leading "-"
STATE_FILE = "nplb_state.json"
ROW_FILE = "schedule_row.json"
GATES = 7
TAIL_LINES = 5


@dataclass(frozen=True)
class Config:
    """The external commands and names, from the environment."""

    crab: str
    preset: str
    squeue: str
    scancel: str
    partition: str
    net_tools: str

    @classmethod
    def from_env(cls) -> Config:
        venv_crab = CHECKOUT / ".venv" / "bin" / "crab"
        default_crab = str(venv_crab) if os.access(venv_crab, os.X_OK) else "crab"
        here = Path(__file__).resolve().parent
        return cls(
            os.environ.get("CRAB") or default_crab,
            os.environ.get("PRESET") or "nplb_h100_gpu",
            os.environ.get("SQUEUE") or "squeue",
            os.environ.get("SCANCEL") or "scancel",
            os.environ.get("PARTITION") or "h100_4nodes",
            os.environ.get("NET_TOOLS") or str(here / "net_tools.sh"),
        )


@dataclass(frozen=True)
class Refusal:
    """One failed gate: its number, name, the first-line reason and the lines that say what to do."""

    gate: int
    name: str
    reason: str
    detail: tuple[str, ...] = ()


def say(text: str) -> None:
    """One narration line on stderr."""
    print(f"[{net_state.now().strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)


def tail(text: str, count: int = TAIL_LINES) -> list[str]:
    """The last `count` non-empty lines of `text`."""
    return [line.rstrip() for line in text.splitlines() if line.strip()][-count:]


# --- pure parts: gates, selectors ----------------------------------------------------------------


def gate_running(runs: list[run_log.Run]) -> Refusal | None:
    """Gate 1: no attempt of any row is still SUBMITTED."""
    running = run_log.running_run(runs)
    if running is None:
        return None
    return Refusal(
        1,
        "running job",
        f"job {running.job} of row {running.n} is still running: nplb follow  (or: nplb cancel)",
        (
            f"job {running.job} of row {running.n} ({running.block} {running.layout} {running.victim})",
            "nplb follow",
            "nplb cancel",
        ),
    )


def gate_row(
    n: int, schedule: list[run_log.Row] | None, camp: Path
) -> tuple[run_log.Row | None, Path | None, Refusal | None]:
    """Gate 2: the row, its block file, or why there are none."""
    if schedule is None:
        return (
            None,
            None,
            Refusal(2, "schedule", "no schedule: run: nplb campaign", ("nplb campaign",)),
        )
    row = next((r for r in schedule if r.n == n), None)
    if row is None:
        last = schedule[-1].n if schedule else 0
        return (
            None,
            None,
            Refusal(
                2,
                "schedule",
                f"row {n} is not in the schedule (1-{last}): nplb next",
                ("nplb next",),
            ),
        )
    block = camp / row.file
    if not block.is_file():
        return (
            row,
            None,
            Refusal(
                2,
                "block file",
                f"block file missing: {block.name}: run: nplb campaign",
                (f"missing: {block}", "nplb campaign"),
            ),
        )
    return row, block, None


def gate_done(row: run_log.Row, runs: list[run_log.Run], force: bool) -> Refusal | None:
    """Gate 3: a DONE row is rerun only with --force."""
    if force or run_log.row_status(row.n, runs) != "DONE":
        return None
    done = [r for r in runs if r.n == row.n and r.status in run_log.FINISHED][-1]
    return Refusal(
        3,
        "done",
        f"row {row.n} is already done ({done.status}, job {done.job}): rerun with: nplb run {row.n} --force",
        (f"nplb run {row.n} --force",),
    )


def gate_declared(
    row: run_log.Row,
    runs: list[run_log.Run],
    schedule: list[run_log.Row],
    declared: dict | None,
    why: str,
) -> Refusal | None:
    """Gate 4: the declared switch state is the row's; the commands are those of `nplb next`."""
    want = (run_log.onoff(row.nplb), run_log.onoff(row.ar))
    if declared is not None and (declared["nplb"], declared["ar"]) == want:
        return None
    if declared is None:
        reason = (
            "no switch state declared" if why == "missing" else f"declared state unreadable: {why}"
        )
    else:
        reason = (
            f"declared nplb={declared['nplb']} ar={declared['ar']}, "
            f"the row needs nplb={want[0]} ar={want[1]}"
        )
    lines = run_log.switch_lines(row, runs, schedule, declared, why, net_state.now())
    return Refusal(4, "switch state", reason, tuple(lines))


def gate_nic(row: run_log.Row, record: dict) -> Refusal | None:
    """Gate 5, second half: the NIC SR bit read by net_tools.sh is the row's."""
    nic = record.get("nic") if isinstance(record.get("nic"), dict) else {}
    have = str(nic.get("sr", "?"))
    if have == row.sr:
        return None
    shown = run_log.onoff(have) if have in ("0", "1") else have
    want = run_log.onoff(row.sr)
    return Refusal(
        5,
        "node state",
        f"the NICs have sr={shown}, the row needs sr={want}: nplb net set sr={want}",
        (f"nplb net set sr={want}",),
    )


def parse_numbers(words: list[str]) -> list[int]:
    """Row numbers and ranges (`5 7 9-12`) as a sorted list without repeats; ValueError on a bad word."""
    numbers: set[int] = set()
    for word in words:
        found = re.fullmatch(r"(\d+)(?:-(\d+))?", word)
        if not found:
            raise ValueError(f"{word!r} is not a row number or a range like 9-12")
        low = int(found.group(1))
        high = int(found.group(2) or low)
        if low < 1 or high < low:
            raise ValueError(f"{word!r} is not a valid row range (rows start at 1)")
        numbers.update(range(low, high + 1))
    return sorted(numbers)


def compress(numbers: list[int]) -> str:
    """[1, 2, 3, 5, 7, 8] as `1-3, 5, 7-8`."""
    parts = []
    start = 0
    while start < len(numbers):
        end = start
        while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
            end += 1
        low, high = numbers[start], numbers[end]
        parts.append(str(low) if low == high else f"{low}-{high}")
        start = end + 1
    return ", ".join(parts)


def select_rows(
    schedule: list[run_log.Row], words: list[str], states: list[str], tiers: list[str]
) -> tuple[list[int], list[str]]:
    """The union of the selectors as row numbers, and the problems (unknown rows, empty selectors)."""
    known = {row.n for row in schedule}
    selected: set[int] = set(parse_numbers(words))
    problems = []
    unknown = sorted(selected - known)
    if unknown:
        problems.append(
            f"unknown rows: {compress(unknown)} (the schedule has 1-{max(known, default=0)})"
        )
    selected &= known
    for label in states:
        rows = {row.n for row in schedule if row.state == label}
        problems += [] if rows else [f"no row has state {label}"]
        selected |= rows
    for tier in tiers:
        rows = {row.n for row in schedule if row.tier == tier}
        problems += [] if rows else [f"no row is in tier {tier}"]
        selected |= rows
    return sorted(selected), problems


def newest_submitted(runs: list[run_log.Run]) -> run_log.Run | None:
    return next((r for r in reversed(runs) if r.status == "SUBMITTED"), None)


def render_refusal(label: str, refusals: list[Refusal]) -> list[str]:
    lines = [f"VERDICT: REFUSED {label}: {refusals[0].reason}"]
    for item in refusals:
        lines += ["", f"gate {item.gate}/{GATES} {item.name}: {item.reason}"]
        lines += [f"  {text}" for text in item.detail]
    return [run_log.cut_line(line) for line in lines]


# --- subprocess parts ----------------------------------------------------------------------------


def net_record(cfg: Config, staging: Path) -> tuple[dict | None, Refusal | None]:
    """Gate 5, first half: `net_tools.sh record STAGING`, then its nplb_state.json."""
    try:
        done = subprocess.run(
            ["bash", cfg.net_tools, "record", str(staging)],
            capture_output=True,
            text=True,
            timeout=600,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, Refusal(
            5, "node state", f"the node state check did not run: {exc}", ("nplb net show",)
        )
    if done.returncode != 0:
        output = tail(done.stdout + "\n" + done.stderr, 8)
        reason = f"the node state check failed (exit {done.returncode})"
        return None, Refusal(5, "node state", reason, (*output, "nplb net show"))
    try:
        record = json.loads((staging / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reason = f"the node state check wrote no readable {STATE_FILE}: {exc}"
        return None, Refusal(5, "node state", reason, ("nplb net show",))
    return (
        (record, None)
        if isinstance(record, dict)
        else (None, Refusal(5, "node state", f"{STATE_FILE} is not an object"))
    )


def gate_validate(cfg: Config, block: Path) -> Refusal | None:
    """Gate 6: `crab validate -p PRESET BLOCK` exits 0."""
    command = f"{Path(cfg.crab).name} validate -p {cfg.preset} {block}"
    try:
        done = subprocess.run(
            [cfg.crab, "validate", "-p", cfg.preset, str(block)],
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            cwd=CHECKOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Refusal(
            6, "crab validate", f"crab validate did not run: {exc}", ("set CRAB=/path/to/crab",)
        )
    if done.returncode == 0:
        return None
    output = tail(done.stdout + "\n" + done.stderr)
    return Refusal(
        6,
        "crab validate",
        f"crab validate exited {done.returncode}",
        (*output, "run it again: " + command),
    )


def gate_partition(cfg: Config, row: int, force: bool) -> tuple[Refusal | None, str]:
    """Gate 7: no job RUNNING or PENDING in the partition. The second value is a note, or ""."""
    command = [cfg.squeue, "-h", "-p", cfg.partition, "-t", "RUNNING,PENDING", "-o", "%i"]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return None, f"{cfg.squeue} not found: the partition was not checked"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{cfg.squeue} failed: {exc}: the partition was not checked"
    if done.returncode != 0:
        first = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[0]
        return None, f"{cfg.squeue} failed ({first}): the partition was not checked"
    jobs = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not jobs or force:
        return None, f"--force: the partition has {len(jobs)} job(s)" if jobs else ""
    return (
        Refusal(
            7,
            "partition",
            f"the partition has {len(jobs)} job(s): nodes are exclusive",
            (
                f"jobs: {' '.join(jobs[:6])}{' ...' if len(jobs) > 6 else ''}",
                "nplb queue",
                f"nplb run {row} --force",
            ),
        ),
        "",
    )


def submit(cfg: Config, block: Path) -> tuple[tuple[str, Path] | None, list[str]]:
    """`crab run -p PRESET --json BLOCK` from the checkout: (job, absolute data dir) or failure lines."""
    try:
        done = subprocess.run(
            [cfg.crab, "run", "-p", cfg.preset, "--json", str(block)],
            capture_output=True,
            text=True,
            timeout=900,
            stdin=subprocess.DEVNULL,
            cwd=CHECKOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f"VERDICT: FAIL submit: crab run did not run: {exc}"]
    if done.returncode != 0:
        lines = [f"VERDICT: FAIL submit: crab run exited {done.returncode}"]
        return None, [*lines, *tail(done.stderr or done.stdout)]
    try:
        data = json.loads(done.stdout)
        job, data_dir = str(data["job_id"]), str(data["data_dir"])
    except (ValueError, KeyError, TypeError):
        job, data_dir = "", ""
    if not JOB_ID.fullmatch(job) or not data_dir:
        lines = ["VERDICT: FAIL submit: crab run printed no job_id and data_dir"]
        return None, [*lines, *tail(done.stdout + "\n" + done.stderr)]
    path = Path(data_dir)
    return (job, path if path.is_absolute() else CHECKOUT / path), []


def record_files(data_dir: Path, row: run_log.Row, staged: Path | None) -> None:
    """nplb_state.json (when a state was recorded) and schedule_row.json into the data dir."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if staged is not None:
            (data_dir / STATE_FILE).write_bytes(staged.read_bytes())
        (data_dir / ROW_FILE).write_text(json.dumps(asdict(row), indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        say(f"warning: could not write the run files into {data_dir}: {exc}")


# --- run -----------------------------------------------------------------------------------------


def check_gates(
    cfg: Config, row: run_log.Row, block: Path | None, runs: list[run_log.Run], force: bool
) -> tuple[list[Refusal], Path | None]:
    """Gates 3 to 7 of a row that exists: the failed ones and the staged state record (None: no gate 5)."""
    found: list[Refusal | None] = [gate_done(row, runs, force)]
    staged: Path | None = None
    if row.state != "-":
        schedule = run_log.read_schedule(run_log.schedule_path())
        declared, why = net_state.read_state(net_state.work_dir() / net_state.DECLARED_FILE)
        found.append(gate_declared(row, runs, schedule, declared, why))
        stamp = net_state.now().strftime("%Y%m%dT%H%M%S")
        staging = net_state.work_dir() / "staging" / f"row{row.n}_{stamp}"
        staging.mkdir(parents=True, exist_ok=True)
        say("reading the network state of the nodes (net_tools.sh record)")
        record, refusal = net_record(cfg, staging)
        found.append(refusal)
        if record is not None:
            found.append(gate_nic(row, record))
            staged = staging / STATE_FILE
    if block is not None:
        say("validating the block with crab")
        found.append(gate_validate(cfg, block))
    refusal, note = gate_partition(cfg, row.n, force)
    found.append(refusal)
    if note:
        say(note)
    return [item for item in found if item is not None], staged


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    path = run_log.schedule_path()
    runs = run_log.read_runs(run_log.runs_path())
    schedule = run_log.read_schedule(path) if path.is_file() else None
    say("checking the gates")
    running = gate_running(runs)
    if args.row == "next":
        nxt = run_log.next_row(schedule, runs) if schedule else None
        if nxt is None and schedule and running is None:
            report = run_log.next_report(schedule, runs, None, "missing", net_state.now())
            print("\n".join(report[: report.index("progress:") - 1]))
            return 0
        n = nxt.n if nxt else 0
    elif args.row.isdigit() and int(args.row) > 0:
        n = int(args.row)
    else:
        raise run_log.LogError(f"usage: nplb run N|next [--force] [--no-follow] (got {args.row!r})")
    refusals = [running] if running else []
    row, block, problem = (
        (None, None, None) if n == 0 and schedule else gate_row(n, schedule, path.parent)
    )
    refusals += [problem] if problem else []
    staged: Path | None = None
    if row is not None:
        more, staged = check_gates(cfg, row, block, runs, args.force)
        refusals += more
    if refusals:
        refusals.sort(key=lambda item: item.gate)
        print("\n".join(render_refusal(f"row {n}" if n else "next", refusals)))
        say(f"refused: {len(refusals)} gate(s) failed, nothing submitted")
        return 1
    assert row is not None and block is not None
    return submit_row(cfg, row, block, staged)


def submit_row(cfg: Config, row: run_log.Row, block: Path, staged: Path | None) -> int:
    """Everything after the gates: submit, record the files, append the attempt, print the result."""
    say(f"submitting row {row.n}: crab run -p {cfg.preset} --json {block.name}")
    sub, failure = submit(cfg, block)
    if sub is None:
        print("\n".join(failure))
        print("nothing was recorded in the run log; fix it, then: " + f"nplb run {row.n}")
        say("submit failed")
        return 1
    job, data_dir = sub
    record_files(data_dir, row, staged)
    attempt = run_log.Run(
        row.n, row.state, row.block, row.layout, row.victim, job, str(data_dir),
        run_log.now_iso(), "SUBMITTED",
    )  # fmt: skip
    try:
        run_log.append_run(run_log.runs_path(), attempt)
    except (run_log.LogError, ValueError, OSError) as exc:
        print(f"FAIL run log: job {job} was submitted but the run log could not be written: {exc}")
        print(f"follow it by its data dir: nplb follow {data_dir}")
        return 1
    say(f"submitted job {job}")
    print(f"submitted: row {row.n} job {job} -> {data_dir}")
    print(f"job {job}")
    return 0


# --- cancel --------------------------------------------------------------------------------------


def confirm(question: str) -> bool:
    """Yes when NPLB_YES=1, or when a person at a terminal answers y; otherwise no."""
    if os.environ.get("NPLB_YES") == "1":
        say(f"{question} yes (NPLB_YES=1)")
        return True
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(f"{question} ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def cmd_cancel(_args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    path = run_log.runs_path()
    run = newest_submitted(run_log.read_runs(path))
    if run is None:
        print("nothing to cancel: no run of the log is SUBMITTED (nplb queue shows the queue)")
        return 1
    what = f"{run.block} {run.layout} {run.victim}"
    print(f"job {run.job} (row {run.n}, {run.state}, {what}) submitted {run.submitted}")
    if not JOB_ID.fullmatch(run.job):
        print(f"FAIL cancel: {run.job!r} is not a job id; edit {path}")
        return 1
    if not confirm("Cancel it? [y/N]"):
        print("declined: nothing changed")
        return 1
    try:
        done = subprocess.run(
            [cfg.scancel, run.job],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL cancel: {cfg.scancel} did not run: {exc}; the run stays SUBMITTED")
        return 1
    if done.returncode != 0:
        first = (tail(done.stderr + "\n" + done.stdout, 8) or [f"exit {done.returncode}"])[0]
        print(f"FAIL cancel: scancel exited {done.returncode}: {first}")
        print("the run stays SUBMITTED; nothing else was touched")
        return 1
    try:
        run_log.update_run(path, run.job, status="CANCELLED", note="cancelled by owner")
    except (KeyError, ValueError, run_log.LogError, OSError) as exc:
        print(f"FAIL cancel: scancel worked but the run log was not updated: {exc}")
        return 1
    say(f"cancelled job {run.job}")
    print(f"cancelled: job {run.job} (row {run.n}); next: nplb next")
    return 0


# --- skip ----------------------------------------------------------------------------------------


def cmd_skip(args: argparse.Namespace) -> int:
    if not (args.rows or args.state or args.tier):
        raise run_log.LogError(
            "usage: nplb skip N... | --state LABEL | --tier T [--why TEXT] [--undo]"
        )
    path = run_log.schedule_path()
    if not path.is_file():
        raise run_log.LogError("no schedule: run: nplb campaign")
    schedule = run_log.read_schedule(path)
    try:
        selected, problems = select_rows(schedule, args.rows, args.state or [], args.tier or [])
    except ValueError as exc:
        raise run_log.LogError(str(exc)) from None
    if problems:
        raise run_log.LogError("; ".join(problems))
    runs = run_log.read_runs(run_log.runs_path())
    by_number = {row.n: row for row in schedule}
    status = {n: run_log.row_status(n, runs) for n in selected}
    if args.undo:
        undone = [n for n in selected if status[n] == "SKIPPED"]
        run_log.set_status_where(run_log.runs_path(), set(undone), "SKIPPED", "CANCELLED")
        print(f"restored {len(undone)} {'row' if len(undone) == 1 else 'rows'}: {compress(undone)}")
        rest = [n for n in selected if n not in undone]
        if rest:
            print(f"not skipped: {compress(rest)}")
        return 0
    why = re.sub(r"[\t\r\n]", " ", args.why).strip() or "skipped by owner"
    todo = [n for n in selected if status[n] not in ("DONE", "RUNNING", "SKIPPED")]
    for n in todo:
        row = by_number[n]
        run_log.append_run(
            run_log.runs_path(),
            run_log.Run(
                n,
                row.state,
                row.block,
                row.layout,
                row.victim,
                "-",
                "-",
                run_log.now_iso(),
                "SKIPPED",
                None,
                why,
            ),
        )
    print(
        f"skipped {len(todo)} {'row' if len(todo) == 1 else 'rows'}: {compress(todo)}".rstrip(": ")
    )
    handled = [f"{n} ({status[n].lower()})" for n in selected if n not in todo]
    if handled:
        print("already handled: " + ", ".join(handled))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NPLB campaign: run a block, cancel it, skip rows."
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    run = sub.add_parser("run", help="gates, submit, record the attempt")
    run.add_argument("row", help="a schedule row number, or next")
    run.add_argument(
        "--force", action="store_true", help="rerun a DONE row; ignore a busy partition"
    )
    run.add_argument(
        "--no-follow", action="store_true", help="the verb's flag; accepted and ignored"
    )
    sub.add_parser("cancel", help="scancel the newest SUBMITTED job of the run log")
    skip = sub.add_parser("skip", help="mark schedule rows as skipped")
    skip.add_argument("rows", nargs="*", help="row numbers and ranges: 5 7 9-12")
    skip.add_argument("--state", action="append", help="every row of this state label")
    skip.add_argument("--tier", action="append", help="every row of this tier")
    skip.add_argument("--why", default="", help="the reason, kept in the run log")
    skip.add_argument("--undo", action="store_true", help="un-skip the selected rows")
    args = parser.parse_args(argv)
    try:
        if args.verb == "run":
            return cmd_run(args)
        return cmd_cancel(args) if args.verb == "cancel" else cmd_skip(args)
    except (run_log.LogError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
