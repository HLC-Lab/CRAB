#!/usr/bin/env python3
"""The campaign bookkeeping: the run log, `nplb next` and `nplb queue`.

    run_log.py next                                  which schedule row to run now (report on stdout)
    run_log.py queue [--squeue-file F [--squeue-exit N]]   the Slurm queue lines plus the last runs

Two files drive it. `schedule.tsv` (written by campaign_plan.py, header `n tier state nplb ar sr block
layout victim est_min switch_change sr_change cum_h file`) lives in $CAMPAIGN_DIR, default
<checkout>/local/campaign. `campaign_runs.tsv` lives in $WORK (else $NPLB_WORK, default
<checkout>/local/nplb-work; not `run_log.tsv`: bringup.sh owns that name) and has one line per
ATTEMPT, appended when a block is submitted and updated in place (tmp file + rename) when it ends:

    n state block layout victim job data_dir submitted status elapsed_s note

`n` is the schedule row, `submitted` an ISO time ($NPLB_NOW or the clock), `status` one of SUBMITTED
OK WARN FAIL CANCELLED SKIPPED, `elapsed_s` an integer or empty while running; tabs and newlines in
a field become spaces. A schedule row is DONE when an attempt is OK or WARN, RUNNING when an attempt is
SUBMITTED, SKIPPED when an attempt is SKIPPED (written by `nplb skip` with job and data_dir `-`; the
owner decided not to run the row), FAILED when it has attempts but none of those; CANCELLED attempts
count as none. `next` proposes the first row with no attempt (failed rows are listed to retry, never
proposed again, skipped rows are neither), compares the network state it needs with the declared one ($WORK/net_state.json), prints the
commands, the progress per tier and a re-estimate of the time left: the estimates of the rows still
to run times (elapsed / estimated) of the finished ones; skipped rows are not in it. Switch and SR
change time is not in it.
`queue` reads squeue's output from a file the verb captured, so this module does no IO of its own on
Slurm; the verb calls `squeue -h -p $PARTITION -o "%i %j %T %M %l %R"` (PARTITION default h100_4nodes).

Importable: read_schedule, read_runs, append_run, update_run, set_status_where, row_status, next_row,
retry_rows, progress, estimate_remaining, now_iso. Reports are plain text; the verbs page them. Exit 0, or 2 on a missing
or bad schedule or runs file or clock (message on stderr). Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
RUNS_FILE = "campaign_runs.tsv"
SCHEDULE_FILE = "schedule.tsv"
SCHEDULE_COLUMNS = (
    "n tier state nplb ar sr block layout victim est_min switch_change sr_change cum_h file".split()
)
RUN_COLUMNS = "n state block layout victim job data_dir submitted status elapsed_s note".split()
STATUSES = ("SUBMITTED", "OK", "WARN", "FAIL", "CANCELLED", "SKIPPED")
FINISHED = ("OK", "WARN")
TIMED = ("OK", "WARN", "FAIL")
PAGE_COLS = 100
LAST_RUNS = 8
QUEUE_LINES = 18
RETRY_LINES = 5
TOGETHER_NOTE = (
    "note   NPLB+AR together is untested on these switches",
    "       if the switch refuses this state, skip its rows: they are the last of each tier",
)


class LogError(Exception):
    """A missing or bad input file or value: the message goes to stderr, the exit code is 2."""


@dataclass(frozen=True)
class Row:
    """One line of schedule.tsv; state, nplb, ar and sr are "-" in a local rehearsal."""

    n: int
    tier: str
    state: str
    nplb: str
    ar: str
    sr: str
    block: str
    layout: str
    victim: str
    est_min: float
    switch_change: int
    sr_change: int
    cum_h: float
    file: str


@dataclass(frozen=True)
class Run:
    """One attempt, one line of campaign_runs.tsv."""

    n: int
    state: str
    block: str
    layout: str
    victim: str
    job: str
    data_dir: str
    submitted: str
    status: str
    elapsed_s: int | None = None
    note: str = ""


@dataclass(frozen=True)
class Estimate:
    ratio: float  # measured / estimated time of the finished attempts, 1.0 with none
    finished: int  # rows that enter the ratio
    remaining_min: float  # estimates of the rows without an OK/WARN attempt
    remaining_h: float  # remaining_min x ratio / 60


@dataclass(frozen=True)
class TierProgress:
    tier: str
    rows: int
    done: int
    failed: int
    elapsed_s: int
    left_h: float
    skipped: int = 0


@dataclass(frozen=True)
class Squeue:
    """What the verb captured: the queue lines, or why there are none ("" when squeue worked)."""

    lines: list[str] | None
    problem: str


# --- files ---------------------------------------------------------------------------------------


def _read_table(path: Path, columns: list[str]) -> list[tuple[int, list[str]]]:
    """(line number, cells) of every data line; LogError on a wrong header or column count."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LogError(f"cannot read {path}: {exc}") from None
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    header = lines[0].split("\t") if lines else []
    for index, want in enumerate(columns):
        got = header[index] if index < len(header) else "(missing)"
        if got != want:
            raise LogError(
                f"bad header in {path}: column {index + 1} is {got!r}, expected {want!r}"
            )
    if len(header) > len(columns):
        raise LogError(f"bad header in {path}: extra column {header[len(columns)]!r}")
    table = []
    for number, line in enumerate(lines[1:], 2):
        if line == "":
            continue
        cells = line.split("\t")
        if len(cells) != len(columns):
            raise LogError(f"{path}:{number}: {len(cells)} columns, expected {len(columns)}")
        table.append((number, cells))
    return table


def read_schedule(path: Path) -> list[Row]:
    """The rows of schedule.tsv. Raises LogError on a bad file."""
    rows = []
    for number, c in _read_table(path, list(SCHEDULE_COLUMNS)):
        try:
            rows.append(
                Row(int(c[0]), *c[1:9], float(c[9]), int(c[10]), int(c[11]), float(c[12]), c[13])
            )
        except ValueError as exc:
            raise LogError(f"{path}:{number}: {exc}") from None
    return rows


def read_runs(path: Path) -> list[Run]:
    """The attempts of campaign_runs.tsv, oldest first; none when the file is missing or empty."""
    if not path.is_file() or path.stat().st_size == 0:
        return []
    runs = []
    for number, c in _read_table(path, list(RUN_COLUMNS)):
        if c[8] not in STATUSES:
            raise LogError(
                f"{path}:{number}: status {c[8]!r}, expected one of {' '.join(STATUSES)}"
            )
        try:
            elapsed = int(c[9]) if c[9] else None
            runs.append(Run(int(c[0]), *c[1:9], elapsed, c[10]))
        except ValueError as exc:
            raise LogError(f"{path}:{number}: {exc}") from None
    return runs


def _cell(value: object) -> str:
    """One tab-free, newline-free cell."""
    return "" if value is None else re.sub(r"[\t\r\n]", " ", str(value)).strip()


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status {status!r}: use one of {' '.join(STATUSES)}")


def append_run(path: Path, run: Run) -> None:
    """Append one attempt (writes the header first in a new file). Raises ValueError on a bad status."""
    _check_status(run.status)
    cells = [_cell(getattr(run, name)) for name in RUN_COLUMNS]
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file() or path.stat().st_size == 0
    if not fresh:
        _read_table(path, list(RUN_COLUMNS))  # refuse to append to a file with another layout
    with path.open("a", encoding="utf-8") as handle:
        handle.write(("\t".join(RUN_COLUMNS) + "\n" if fresh else "") + "\t".join(cells) + "\n")


def update_run(path: Path, job: str, **fields: object) -> None:
    """Set columns of every attempt of Slurm job `job`, through a tmp file and a rename.

    Raises KeyError when no attempt has that job, ValueError on an unknown column or bad status.
    """
    for name, value in fields.items():
        if name not in RUN_COLUMNS:
            raise ValueError(f"unknown column {name!r}")
        if name == "status":
            _check_status(str(value))
    if not path.is_file():
        raise KeyError(f"no run with job {job}: {path} does not exist")
    _read_table(path, list(RUN_COLUMNS))
    lines = path.read_text(encoding="utf-8").split("\n")
    found = False
    for index, line in enumerate(lines[1:], 1):
        cells = line.split("\t")
        if len(cells) == len(RUN_COLUMNS) and cells[RUN_COLUMNS.index("job")] == job:
            for name, value in fields.items():
                cells[RUN_COLUMNS.index(name)] = _cell(value)
            lines[index] = "\t".join(cells)
            found = True
    if not found:
        raise KeyError(f"no run with job {job} in {path}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def set_status_where(path: Path, numbers: set[int], old: str, new: str) -> list[int]:
    """Turn the attempts of schedule rows `numbers` with status `old` into `new`; the rows changed.

    Attempts with no Slurm job (job `-`, the SKIPPED ones) cannot be found by update_run, hence this.
    Raises ValueError on a bad status; nothing is written when no attempt matches.
    """
    _check_status(old)
    _check_status(new)
    if not path.is_file():
        return []
    _read_table(path, list(RUN_COLUMNS))
    lines = path.read_text(encoding="utf-8").split("\n")
    changed: set[int] = set()
    for index, line in enumerate(lines[1:], 1):
        cells = line.split("\t")
        if len(cells) != len(RUN_COLUMNS) or cells[RUN_COLUMNS.index("status")] != old:
            continue
        if int(cells[0]) in numbers:
            cells[RUN_COLUMNS.index("status")] = new
            lines[index] = "\t".join(cells)
            changed.add(int(cells[0]))
    if changed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return sorted(changed)


def now_iso() -> str:
    """The submit time of a new attempt: $NPLB_NOW, else the clock (YYYY-MM-DDTHH:MM:SS)."""
    return net_state.now().strftime(net_state.ISO)


def runs_path() -> Path:
    return net_state.work_dir() / RUNS_FILE


def schedule_path() -> Path:
    explicit = os.environ.get("CAMPAIGN_DIR")
    return (Path(explicit) if explicit else CHECKOUT / "local" / "campaign") / SCHEDULE_FILE


# --- pure logic ----------------------------------------------------------------------------------


def attempts(runs: list[Run], n: int) -> list[Run]:
    """The attempts of schedule row n, cancelled ones left out."""
    return [r for r in runs if r.n == n and r.status != "CANCELLED"]


def row_status(n: int, runs: list[Run]) -> str:
    """DONE, RUNNING, SKIPPED, FAILED, or NEW (no attempt)."""
    found = attempts(runs, n)
    statuses = {r.status for r in found}
    if statuses & set(FINISHED):
        return "DONE"
    if "SUBMITTED" in statuses:
        return "RUNNING"
    if "SKIPPED" in statuses:
        return "SKIPPED"
    return "FAILED" if found else "NEW"


def next_row(schedule: list[Row], runs: list[Run]) -> Row | None:
    """The first row with no attempt (failed rows are not proposed again), None when none is left."""
    for row in schedule:
        if row_status(row.n, runs) == "NEW":
            return row
    return None


def retry_rows(schedule: list[Row], runs: list[Run]) -> list[Row]:
    return [row for row in schedule if row_status(row.n, runs) == "FAILED"]


def running_run(runs: list[Run]) -> Run | None:
    """The first attempt still SUBMITTED."""
    return next((r for r in runs if r.status == "SUBMITTED"), None)


def estimate_remaining(schedule: list[Row], runs: list[Run]) -> Estimate:
    """Time left: estimates of the rows without an OK/WARN attempt times the measured ratio."""
    est = {row.n: row.est_min for row in schedule}
    done = [r for r in runs if r.status in FINISHED and r.elapsed_s is not None and r.n in est]
    measured = sum(r.elapsed_s or 0 for r in done)
    estimated = sum(est[r.n] * 60 for r in done)
    ratio = measured / estimated if estimated > 0 else 1.0
    left = sum(
        row.est_min for row in schedule if row_status(row.n, runs) not in ("DONE", "SKIPPED")
    )
    return Estimate(ratio, len({r.n for r in done}), left, left * ratio / 60)


def progress(schedule: list[Row], runs: list[Run]) -> list[TierProgress]:
    """One entry per tier of the schedule, in schedule order."""
    ratio = estimate_remaining(schedule, runs).ratio
    result = []
    for tier in dict.fromkeys(row.tier for row in schedule):
        rows = [row for row in schedule if row.tier == tier]
        statuses = [row_status(row.n, runs) for row in rows]
        numbers = {row.n for row in rows}
        elapsed = sum(r.elapsed_s or 0 for r in runs if r.n in numbers and r.status in TIMED)
        left = sum(row.est_min for row, s in zip(rows, statuses) if s not in ("DONE", "SKIPPED"))
        result.append(
            TierProgress(
                tier,
                len(rows),
                statuses.count("DONE"),
                statuses.count("FAILED"),
                elapsed,
                left * ratio / 60,
                statuses.count("SKIPPED"),
            )
        )
    return result


def onoff(bit: str) -> str:
    return "on" if bit == "1" else "off"


def cut_line(line: str) -> str:
    return line if len(line) <= PAGE_COLS else line[: PAGE_COLS - 1] + "~"


def describe(row: Row) -> str:
    return f"{row.block} {row.layout} {row.victim}"


def switch_lines(
    row: Row, runs: list[Run], schedule: list[Row], declared: dict | None, why: str, now: datetime
) -> list[str]:
    """What the row needs, what is declared, and the commands to get there.

    The SR command is listed when nothing ran yet or the last attempt (not cancelled) ran a row
    with another SR.
    """
    if row.state == "-":
        return ["needs  no network state (local rehearsal)"]
    want = (onoff(row.nplb), onoff(row.ar))
    sr = onoff(row.sr)
    lines = [f"needs  nplb={want[0]} ar={want[1]} sr={sr}"]
    last = next((r for r in reversed(runs) if r.status != "CANCELLED"), None)
    previous = next(
        (r2 for r2 in schedule if last is not None and r2.n == last.n), None
    )  # None: unknown
    set_sr = [f"  nplb net set sr={sr}"] if previous is None or previous.sr != row.sr else []
    sheet = f"nplb={want[0]} ar={want[1]}"
    commands = [f"  nplb net sheet {sheet}", f"  nplb net declare {sheet}", *set_sr]
    if declared is None:
        head = (
            "no switch state declared" if why == "missing" else f"declared state unreadable: {why}"
        )
        lines += [head, "paste the sheet, then declare:", *commands]
    else:
        age = net_state.age_text(declared["declared_at"], now).removeprefix("declared ")
        lines.append(f"declared nplb={declared['nplb']} ar={declared['ar']} ({age})")
        if (declared["nplb"], declared["ar"]) == want:
            lines.append("declared state matches: no switch change needed")
            if set_sr:
                lines += ["set SR, run:", *set_sr]
        else:
            lines += ["MISMATCH on the switches: paste the sheet, then declare:", *commands]
    if row.nplb == "1" and row.ar == "1":
        lines += TOGETHER_NOTE
    return lines


def next_report(
    schedule: list[Row], runs: list[Run], declared: dict | None, why: str, now: datetime
) -> list[str]:
    """The `nplb next` report, the verdict first."""
    running = running_run(runs)
    row = next_row(schedule, runs)
    retry = retry_rows(schedule, runs)
    lines: list[str]
    if running is not None:
        lines = [
            f"VERDICT: RUNNING row {running.n} (job {running.job}): follow it with: nplb follow"
        ]
    elif row is not None:
        est = f"est {row.est_min:.1f} min"
        lines = [
            f"VERDICT: NEXT row {row.n}/{len(schedule)}: tier {row.tier}, {row.state}, {describe(row)} ({est})"
        ]
        lines += switch_lines(row, runs, schedule, declared, why, now)
        lines.append(f"then: nplb run {row.n}")
    elif retry:
        noun = "row" if len(retry) == 1 else "rows"
        lines = [f"VERDICT: RETRY {len(retry)} failed {noun}, nothing new to run"]
    else:
        skipped = sum(1 for row in schedule if row_status(row.n, runs) == "SKIPPED")
        tail = f", {skipped} skipped" if skipped else ""
        lines = [f"VERDICT: DONE all {len(schedule) - skipped} rows{tail}"]
    if retry:
        lines += ["", "to retry (failed, not proposed again; rerun with nplb run N):"]
        for item in retry[:RETRY_LINES]:
            lines.append(f"  row {item.n}  {item.state}  {describe(item)}")
        if len(retry) > RETRY_LINES:
            lines.append(f"  +{len(retry) - RETRY_LINES} more")
    est_all = estimate_remaining(schedule, runs)
    tiers = progress(schedule, runs)
    lines += ["", "progress:"]
    show_skipped = any(t.skipped for t in tiers)  # the column appears once something is skipped
    for t in tiers:
        skipped = t.skipped if show_skipped else None
        lines.append(
            _progress_line(
                f"tier {t.tier}", t.rows, t.done, t.failed, t.elapsed_s, t.left_h, skipped
            )
        )
    lines.append(
        _progress_line(
            "total ",
            sum(t.rows for t in tiers),
            sum(t.done for t in tiers),
            sum(t.failed for t in tiers),
            sum(t.elapsed_s for t in tiers),
            sum(t.left_h for t in tiers),
            sum(t.skipped for t in tiers) if show_skipped else None,
        )
    )
    basis = f"from {est_all.finished} finished rows" if est_all.finished else "no finished run yet"
    sep = " " if est_all.finished else ": "
    lines += [
        "",
        f"remaining  {est_all.remaining_h:.1f} h (estimate x ratio {est_all.ratio:.2f}{sep}{basis})",
    ]
    lines.append("switch and SR change time is not included (cum_h of schedule.tsv has it)")
    return lines


def _progress_line(
    name: str,
    rows: int,
    done: int,
    failed: int,
    elapsed_s: int,
    left_h: float,
    skipped: int | None = None,
) -> str:
    column = "" if skipped is None else f"  {skipped} skipped"
    return f"{name}  {done}/{rows} done  {failed} failed{column}  {elapsed_s / 3600:.1f} h elapsed  {left_h:.1f} h left"


def queue_report(runs: list[Run], squeue: Squeue, partition: str) -> list[str]:
    """The `nplb queue` report: the queue lines, then the last runs newest first."""
    lines = []
    if squeue.lines is None:
        lines.append("VERDICT: WARN queue unknown (see below)")
        lines.append(squeue.problem)
    elif not squeue.lines:
        lines.append(f"VERDICT: QUEUE empty in {partition}")
    else:
        noun = "job" if len(squeue.lines) == 1 else "jobs"
        lines.append(f"VERDICT: QUEUE {len(squeue.lines)} {noun} in {partition}")
        lines.append("jobid name state time limit reason:")
        lines += [cut_line(x) for x in squeue.lines[:QUEUE_LINES]]
        if len(squeue.lines) > QUEUE_LINES:
            lines.append(f"+{len(squeue.lines) - QUEUE_LINES} more")
    lines.append("last runs:")
    if not runs:
        return [*lines, "no runs yet"]
    names = ["n", "state", "block", "victim", "job", "status", "elapsed"]
    table = [names]
    for r in reversed(runs[-LAST_RUNS:]):
        elapsed = "-" if r.elapsed_s is None else f"{r.elapsed_s}s"
        table.append([str(r.n), r.state or "-", r.block, r.victim, r.job, r.status, elapsed])
    widths = [max(len(row[i]) for row in table) for i in range(len(names))]
    return lines + [
        cut_line("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip()) for row in table
    ]


# --- command line --------------------------------------------------------------------------------


def read_squeue(file: str | None, code: int) -> Squeue:
    """Turn the captured squeue output (None: squeue is not installed) into a Squeue."""
    if file is None:
        return Squeue(None, "squeue not found: cannot show the queue")
    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LogError(f"cannot read the squeue output {file}: {exc}") from None
    lines = [line for line in text.splitlines() if line.strip()]
    if code != 0:
        return Squeue(None, lines[0] if lines else f"squeue failed (exit {code})")
    return Squeue(lines, "")


def cmd_next(_args: argparse.Namespace) -> list[str]:
    path = schedule_path()
    if not path.is_file():
        raise LogError("no schedule: run: nplb campaign")
    schedule = read_schedule(path)
    if not schedule:
        raise LogError(f"{path} has no rows: run: nplb campaign")
    runs = read_runs(runs_path())
    declared, why = net_state.read_state(net_state.work_dir() / net_state.DECLARED_FILE)
    return next_report(schedule, runs, declared, why, net_state.now())


def cmd_queue(args: argparse.Namespace) -> list[str]:
    partition = os.environ.get("PARTITION") or "h100_4nodes"
    return queue_report(
        read_runs(runs_path()), read_squeue(args.squeue_file, args.squeue_exit), partition
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NPLB campaign run log: next and queue reports.")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("next", help="which schedule row to run now")
    queue = sub.add_parser("queue", help="the Slurm queue lines and the last runs")
    queue.add_argument(
        "--squeue-file", help="squeue's captured output; absent: squeue is not installed"
    )
    queue.add_argument(
        "--squeue-exit", type=int, default=0, help="squeue's exit status (default 0)"
    )
    args = parser.parse_args(argv)
    try:
        lines = cmd_next(args) if args.verb == "next" else cmd_queue(args)
    except (LogError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
