#!/usr/bin/env python3
"""Behind `nplb smoke`: run the smoke blocks one after the other and print one verdict page.

    smoke_run.py run [FEATURE...]

Plans the blocks (`smoke.py plan`, into <WORK>/smoke/campaign), then runs `nplb run N` for every row in
table order and waits for it to end (`nplb run` follows its job). The smoke test has a work folder of
its own, <WORK>/smoke (WORK is `$WORK`, else `$NPLB_WORK`, else `<checkout>/local/nplb-work`): every
child runs with WORK=<WORK>/smoke, CAMPAIGN_DIR=<WORK>/smoke/campaign and
NPLB_REPORT_DIR=<WORK>/smoke/reports, so its run log and its pages `run-1`.. never mix with the
campaign's. A new run first MOVES the old `campaign_runs.tsv` and `campaign/` into
`archive/<YYYYmmdd-HHMMSS>/` and refuses to start while the smoke run log has a SUBMITTED attempt.

The features are independent: after OK, WARN or FAIL the next row runs. The driver stops when `nplb
run` logged no attempt (a refusal or a failed submit) or left it SUBMITTED (Ctrl-C, a lost follow); the
rest is `not run`. It never forces a row. The child's stdout goes to OUR stderr, so the live run
pages are seen and only the summary is paged. Stdout is `#report smoke` and the summary, verdict first.

Env: NPLB_BIN (the dispatcher, default `nplb` next to this file), NPLB_NOW, and those of `nplb run`.
Exit 0 when every feature is OK or WARN, 1 for FAIL, INCOMPLETE or a refusal to start, 2 for a bad
feature or an unreadable file. Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402
import run_log  # noqa: E402
import smoke  # noqa: E402
from campaign import UsageError  # noqa: E402

HERE = Path(__file__).resolve().parent
PASSED = ("OK", "WARN")
DONE = ("OK", "WARN", "FAIL")
NOT_RUN = "not run"


@dataclass
class Result:
    """One feature of the table: what the newest attempt of its row says."""

    n: int
    feature: str
    result: str = NOT_RUN  # OK WARN FAIL, running (still SUBMITTED) or not run
    elapsed_s: int | None = None
    job: str = ""
    data_dir: str = ""


@dataclass
class Stop:
    """Why the driver did not reach the end of the table."""

    n: int
    reason: str
    job: str = ""  # the job that keeps running, when there is one


def say(text: str) -> None:
    print(f"[{net_state.now().strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)


def smoke_work() -> Path:
    """<WORK>/smoke, the folder `smoke.py plan` also uses (`<WORK>/smoke/campaign`)."""
    return smoke.default_out(os.environ).parent


def minutes(seconds: float) -> str:
    return "under 1 min" if seconds < 60 else f"{seconds / 60:.0f} min"


def clock(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m{seconds % 60:02d}s"


# --- pure part: the summary page -----------------------------------------------------------------------


def verdict_line(results: list[Result], stop: Stop | None, took_s: float) -> str:
    total = len(results)
    failed = [r.feature for r in results if r.result == "FAIL"]
    warned = [r.feature for r in results if r.result == "WARN"]
    if stop is not None:
        done = sum(1 for r in results if r.result in DONE)
        return f"INCOMPLETE smoke: stopped at row {stop.n} ({stop.reason}), {done} of {total} done"
    if failed:
        return f"FAIL smoke: {', '.join(failed)} failed ({total - len(failed)} of {total} passed)"
    line = f"OK smoke: {total} of {total} features passed ({minutes(took_s)})"
    return line + (f", WARN: {', '.join(warned)}" if warned else "")


def summary_lines(results: list[Result], stop: Stop | None, took_s: float, work: Path) -> list[str]:
    proves = {f.name: f.proves for f in smoke.FEATURES}
    lines = [f"VERDICT: {verdict_line(results, stop, took_s)}", ""]
    lines.append(f"{'feature':<9}{'result':<9}{'time':<7}proves")
    for r in results:
        lines.append(f"{r.feature:<9}{r.result:<9}{clock(r.elapsed_s):<7}{proves[r.feature]}")
    failed = [r for r in results if r.result == "FAIL"]
    if failed:
        lines.append(f"next: nplb diag run {Path(failed[0].data_dir).name}")
        lines.append("data folders of the failed features:")
        lines += [f"  {r.feature:<8}{Path(r.data_dir).name}" for r in failed]
    if stop is not None and stop.job:
        lines.append(f"job {stop.job} keeps running: nplb smoke follow, or nplb smoke cancel")
        lines.append("the rows after it did not run; nplb smoke starts the whole test again")
    elif stop is not None:
        lines.append(f"nothing was submitted for row {stop.n}: the page above says why")
        lines.append("fix that, then: nplb smoke")
    elif not failed:
        lines.append("next: nplb topo, then nplb campaign")
        note = "smoke runs use state '-': nplb report does not list them"
        one_line = f"{note}; the pages are in {work}"
        lines += [one_line] if len(one_line) <= run_log.PAGE_COLS else [note, f"pages: {work}"]
    return lines


# --- the driver ----------------------------------------------------------------------------------------


def archive_old(work: Path) -> Path | None:
    """Move the old run log and campaign folder into archive/<stamp>/; the folder, or None."""
    old = [p for p in (work / run_log.RUNS_FILE, work / "campaign") if p.exists()]
    if not old:
        return None
    stamp = net_state.now().strftime("%Y%m%d-%H%M%S")
    dest = work / "archive" / stamp
    count = 1
    while dest.exists():
        count += 1
        dest = work / "archive" / f"{stamp}-{count}"
    dest.mkdir(parents=True)
    for path in old:
        shutil.move(str(path), str(dest / path.name))
    return dest


def plan_blocks(names: list[str]) -> int:
    """`smoke.py plan` into the default folder; its report is not wanted here."""
    with contextlib.redirect_stdout(io.StringIO()):
        return smoke.main(["plan", *names])


def run_row(n: int, work: Path) -> int:
    """`nplb run N` under the smoke env; its stdout goes to our stderr. Ctrl-C waits for the child."""
    env = dict(os.environ)
    env["WORK"] = str(work)
    env["CAMPAIGN_DIR"] = str(work / "campaign")
    env["NPLB_REPORT_DIR"] = str(work / "reports")
    binary = os.environ.get("NPLB_BIN") or str(HERE / "nplb")
    child = subprocess.Popen(["bash", binary, "run", str(n)], env=env, stdout=sys.stderr)
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            say("Ctrl-C: waiting for nplb run to stop")


def drive(work: Path) -> tuple[list[Result], Stop | None]:
    schedule = run_log.read_schedule(work / "campaign" / run_log.SCHEDULE_FILE)
    results = [Result(row.n, row.block) for row in schedule]
    total_min = sum(row.est_min for row in schedule)
    say(f"planned {len(schedule)} features, about {total_min:.0f} min in all")
    for row, result in zip(schedule, results, strict=True):
        say(f"row {row.n} of {len(schedule)}: {row.block} starts (about {row.est_min:g} min)")
        code = run_row(row.n, work)
        found = run_log.attempts(run_log.read_runs(work / run_log.RUNS_FILE), row.n)
        if not found:
            say(f"row {row.n}: nothing submitted (nplb run exited {code})")
            return results, Stop(row.n, f"nothing submitted, nplb run exited {code}")
        last = found[-1]
        result.job, result.data_dir, result.elapsed_s = last.job, last.data_dir, last.elapsed_s
        if last.status not in DONE:
            result.result = "running"
            say(f"row {row.n}: job {last.job} is still {last.status} (nplb run exited {code})")
            return results, Stop(row.n, f"job {last.job} still running", last.job)
        result.result = last.status
        say(f"row {row.n} {row.block}: {last.status} ({clock(last.elapsed_s)})")
    return results, None


def cmd_run(names: list[str]) -> int:
    try:
        smoke.select(names)
    except UsageError as exc:
        print(f"smoke.py: error: {exc}", file=sys.stderr)
        return 2
    work = smoke_work()
    running = run_log.running_run(run_log.read_runs(work / run_log.RUNS_FILE))
    if running is not None:
        print(
            f"a smoke job is still running (job {running.job}): "
            "nplb smoke follow, or nplb smoke cancel",
            file=sys.stderr,
        )
        return 1
    started = time.monotonic()
    archived = archive_old(work)
    if archived is not None:
        say(f"the old smoke files were moved to {archived}")
    say(f"planning the smoke blocks in {work / 'campaign'}")
    if plan_blocks(names) != 0:
        return 2
    results, stop = drive(work)
    lines = summary_lines(results, stop, time.monotonic() - started, work / "reports")
    print("#report smoke")
    print("\n".join(lines))
    return 0 if stop is None and all(r.result in PASSED for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the NPLB smoke blocks one by one.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="plan, run every row, print the verdict page")
    run.add_argument("features", nargs="*", help="features to run (default: all)")
    args = parser.parse_args(argv)
    try:
        return cmd_run(args.features)
    except (run_log.LogError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        say("interrupted: no summary; the run log in the smoke work folder has what was logged")
        return 130


if __name__ == "__main__":
    sys.exit(main())
