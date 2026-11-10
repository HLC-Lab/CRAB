#!/usr/bin/env python3
"""`nplb diag run`: why did a run fail? One paged report from a finished (or failed) run folder.

    diag_run.py [TARGET]      TARGET as for `nplb follow`: a schedule row, a Slurm job id, a data dir
                              path or the name of a folder under $DATA_ROOT; none = the newest submitted
                              attempt of the run log, else the newest attempt, else the newest folder

Read only: it opens files of the run folder and writes nothing. Stdout is `#report diag-run-NAME` and the
report (the verb pages and saves it): the verdict first (OK, FAIL or UNKNOWN), the cause class and the
next command, the failed apps with their exit code and first error line, the distinct UCX / MPI / CUDA
lines with repeat counts, and the last 8 lines of slurm_error.log. The cause is the first class of CAUSES
that matches the error logs, else `unclassified`: a log it does not know is never given a made-up cause.
Reads <data_dir>/slurm_output.log (the engine's FAILED lines give the exit codes), slurm_error.log,
<exp>/error_app_N.log, config.json (the message size of a failed app) and, through follow.py, metadata.csv.
Env: DATA_ROOT (default <checkout>/data/nplb_h100_gpu), WORK, PAGE_COLS (default 100).
Exit 0 for OK, 1 for FAIL and UNKNOWN, 2 when the target is wrong (message on stderr). Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import follow  # noqa: E402
import net_state  # noqa: E402
import run_log  # noqa: E402


@dataclass(frozen=True)
class Cause:
    label: str
    pattern: re.Pattern[str]


# Tried in this order, first hit wins. Every pattern is a guess except the ibv_reg_mr one, which is from
# the real October 2026 log of the all-to-all run (tests/fixtures/nplb/a2a_run1_error_app_10.log).
CAUSES = (
    Cause("memory registration failed", re.compile(r"ibv_reg_mr|Bad address")),
    Cause(
        "GPU out of memory", re.compile(r"cudaErrorMemoryAllocation|out of memory", re.IGNORECASE)
    ),
    Cause(
        "network / UCX transport error",
        re.compile(r"Transport retry count exceeded|Endpoint timeout|(?:ucp_|UCX\s+ERROR).*(?i:timeout)"),
    ),
    Cause("crash (segfault or signal)", re.compile(r"Segmentation fault|signal 11|Bus error")),
    Cause("time limit", re.compile(r"DUE TO TIME LIMIT|TIMEOUT")),
)
UNCLASSIFIED = "unclassified"
INTEREST = re.compile(
    r"UCX|ucx_|mlx5|ibv_|MPI_ERR|MPI_Abort|CUDA|cuda|Segmentation|signal \d+|Transport retry|timeout"
)
STAMPS = re.compile(r"\[\d+\.\d+\]|\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?|\[\d\d:\d\d:\d\d\]")
HOSTS = re.compile(r"\[([A-Za-z][\w.-]*):\d+(?::\d+)?\]")
HEX = re.compile(r"0x[0-9a-fA-F]+")
SIZE_ARG = re.compile(r"(?:^|\s)-msgsize\s+(\d+)")
SIZE_LINE = re.compile(r"msg-size:\s*(\d+)")
HEADER_EXIT = re.compile(r"^App \d+ exit=(-?\d+)")
SCAN_BYTES = 8 * 1024 * 1024  # of slurm_error.log, from its end
APP_LOG_BYTES = 256 * 1024  # of each error_app_N.log, from its start
MAX_APP_LOGS = 50
MAX_DISTINCT = 500
MAX_ROWS = 10
TAIL_LINES = 8
ERROR_LINES_PER_ROW = 2
GUESS_NOTE = "note: the log shapes of this case are guesses until a real log is seen (only ibv_reg_mr is real)"


@dataclass(frozen=True)
class FailedApp:
    exp: str
    app: str
    code: str
    error: str  # the first error line of its error_app_N.log, "" when there is none


@dataclass(frozen=True)
class Evidence:
    data_dir: Path
    failed: list[FailedApp]
    experiments: int  # experiments the worker log mentions, 0 when unknown
    ended: str  # follow.classify_last_line of slurm_output.log, "missing" without the file
    has_error_log: bool
    app_texts: dict[tuple[str, str], str]  # (exp, app) -> head of its error_app_N.log
    error_text: str  # the end of slurm_error.log, no colour codes
    metadata: tuple[str, list[str]]  # follow.metadata_failures


def page_width() -> int:
    try:
        return max(30, int(os.environ.get("PAGE_COLS") or follow.PAGE_COLS))
    except ValueError:
        return follow.PAGE_COLS


def read_head(path: Path, size: int) -> str:
    try:
        with path.open("rb") as handle:
            return follow.strip_ansi(handle.read(size).decode("utf-8", errors="replace"))
    except OSError:
        return ""


def read_error_log(path: Path) -> str:
    """The last SCAN_BYTES of a log as text, without the first line when the file was cut."""
    text = follow.strip_ansi(follow.read_tail(path, SCAN_BYTES))
    try:
        cut = path.stat().st_size > SCAN_BYTES
    except OSError:
        return ""
    return text.split("\n", 1)[-1] if cut else text


def read_output_log(path: Path) -> tuple[follow.RunBook, str]:
    """The engine's account of the run and how the log ends ("missing" without the file)."""
    book = follow.RunBook()
    last = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                book.feed(raw)
                if raw.strip():
                    last = raw
    except OSError:
        return book, "missing"
    return book, follow.classify_last_line(follow.last_line(follow.strip_ansi(last)))


def app_logs(data_dir: Path) -> list[tuple[str, str, Path]]:
    found = [
        (path.parent.name, path.stem.removeprefix("error_app_"), path)
        for path in data_dir.glob("*/error_app_*.log")
    ]
    return sorted(found, key=lambda x: (x[0], int(x[1]) if x[1].isdigit() else -1, x[1]))


def gather(data_dir: Path) -> Evidence:
    book, ended = read_output_log(data_dir / "slurm_output.log")
    logs = app_logs(data_dir)[:MAX_APP_LOGS]
    texts = {(exp, app): read_head(path, APP_LOG_BYTES) for exp, app, path in logs}
    failed: list[FailedApp] = []
    for (exp, app), state in book.apps.items():
        if state == "failed":
            code = book.codes.get((exp, app), "?")
            failed.append(FailedApp(exp, app, code, follow.first_error_line(data_dir, exp, app)))
    seen = {(f.exp, f.app) for f in failed}
    for (exp, app), text in texts.items():
        if (exp, app) in seen:
            continue
        header = HEADER_EXIT.match(text.lstrip())
        code = header.group(1) if header else "?"
        failed.append(FailedApp(exp, app, code, follow.first_error_line(data_dir, exp, app)))
    error_path = data_dir / "slurm_error.log"
    return Evidence(
        data_dir,
        failed,
        len(book.experiments),
        ended,
        error_path.is_file(),
        texts,
        read_error_log(error_path),
        follow.metadata_failures(data_dir),
    )


def classify(texts: list[str]) -> tuple[str, str]:
    """(label, the text that matched) of the first cause class that hits, else (UNCLASSIFIED, "")."""
    text = "\n".join(texts)
    for cause in CAUSES:
        found = cause.pattern.search(text)
        if found:
            return cause.label, " ".join(found.group(0).split())
    return UNCLASSIFIED, ""


def normalise(line: str) -> tuple[str, str]:
    """(key, shown): LINE without timestamps, hosts and pids; the key also hides addresses."""
    shown = " ".join(HOSTS.sub("", STAMPS.sub("", line)).split())
    return HEX.sub("0x", shown), shown


def interesting(ev: Evidence) -> tuple[list[tuple[int, str]], list[str]]:
    """(distinct UCX/MPI/CUDA lines with counts in order of appearance, the hosts that appear).
    New distinct lines past MAX_DISTINCT are not counted."""
    counts: dict[str, int] = {}
    shown_by_key: dict[str, str] = {}
    hosts: list[str] = []
    for text in [*ev.app_texts.values(), ev.error_text]:
        for line in text.splitlines():
            if not INTEREST.search(line):
                continue
            for host in HOSTS.findall(line):
                if host not in hosts:
                    hosts.append(host)
            key, shown = normalise(line)
            if key in counts:
                counts[key] += 1
            elif len(counts) < MAX_DISTINCT:
                counts[key] = 1
                shown_by_key[key] = shown
    return [(counts[key], shown_by_key[key]) for key in counts], hosts


def tail_lines(ev: Evidence) -> list[str]:
    lines = [" ".join(x.split()) for x in ev.error_text.splitlines() if x.strip()]
    return lines[-TAIL_LINES:]


def config_sizes(ev: Evidence) -> list[str]:
    """The distinct message sizes of the failed apps: from config.json, else the `msg-size:` line of
    their error log; ascending."""
    try:
        experiments = json.loads((ev.data_dir / "config.json").read_text(encoding="utf-8"))[
            "experiments"
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        experiments = {}
    sizes: set[int] = set()
    for item in ev.failed:
        found = None
        try:
            apps = experiments[item.exp]["apps"]
            entry = apps[item.app] if isinstance(apps, dict) else apps[int(item.app)]
            found = SIZE_ARG.search(str(entry["args"]))
        except (KeyError, IndexError, ValueError, TypeError):
            pass
        if found is None:
            found = SIZE_LINE.search(ev.app_texts.get((item.exp, item.app), ""))
        if found:
            sizes.add(int(found.group(1)))
    return [str(x) for x in sorted(sizes)]


def next_step(ev: Evidence, cause: str, hosts: list[str]) -> str:
    host = hosts[0] if hosts else "HOST"
    error_log = ev.data_dir / "slurm_error.log"
    if cause == CAUSES[0].label:
        sizes = config_sizes(ev)
        what = f"msg size {', '.join(sizes[:3])}" if sizes else "the failed apps"
        return f"next: {what} fails memory registration on the nodes: nplb node diag {host}"
    if cause == CAUSES[1].label:
        return f"next: is another job holding GPU memory? nplb node diag {host}"
    if cause == CAUSES[2].label:
        return f"next: nplb diag counters, then nplb node diag {host}"
    if cause == UNCLASSIFIED:
        return f"next: read {error_log if ev.has_error_log else ev.data_dir}"
    return f"next: read the end of {error_log}"


def wrapped(
    text: str, width: int, first: str = "", rest: str = "", max_lines: int = 0
) -> list[str]:
    """TEXT wrapped to WIDTH columns; at most MAX_LINES lines (0: all), the last one ending in "~"."""
    lines = textwrap.wrap(
        text,
        width=width,
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not lines:
        return [first.rstrip()]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1] + "~"
    return lines


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def decide(ev: Evidence, cause: str) -> tuple[str, str]:
    """(verdict, the clause after the folder name)."""
    if ev.failed:
        exps = len({f.exp for f in ev.failed})
        scope = f"{exps} of {ev.experiments}" if ev.experiments >= exps else str(exps)
        return "FAIL", f"{plural(len(ev.failed), 'failed app')} in {scope} experiments, {cause}"
    if ev.ended == "ok":
        state, names = ev.metadata
        if state == "failed":
            return "FAIL", "no failed app in the log, but metadata.csv lists " + ", ".join(names)
        return "OK", "no failed app"
    if ev.error_text.strip() or ev.ended in ("fail", "terminated"):
        return "FAIL", f"no failed app, the run did not finish, {cause}"
    if ev.ended == "missing":
        missing = [n for n in ("slurm_output.log", "slurm_error.log") if not (ev.data_dir / n).is_file()]
        return "UNKNOWN", f"missing {', '.join(missing)}: nothing to read"
    return "UNKNOWN", "slurm_output.log has no final line and slurm_error.log is empty or missing"


def failed_table(ev: Evidence, width: int) -> list[str]:
    exp_width = min(24, max(10, *(len(f.exp) for f in ev.failed)))
    head = f"  {'experiment':<{exp_width}}  {'app':>4}  {'exit':>4}  "
    lines = [f"failed apps ({len(ev.failed)}):", head + "first error line"]
    for item in ev.failed[:MAX_ROWS]:
        prefix = f"  {follow.cut_text(item.exp, exp_width):<{exp_width}}  {item.app:>4}  {item.code:>4}  "
        error = item.error or "(no error_app log)"
        lines += wrapped(error, width, prefix, " " * len(prefix), ERROR_LINES_PER_ROW)
    if len(ev.failed) > MAX_ROWS:
        lines.append(f"  ... {len(ev.failed) - MAX_ROWS} more")
    return lines


def build_report(ev: Evidence, width: int) -> tuple[str, list[str]]:
    distinct, hosts = interesting(ev)
    texts = [*ev.app_texts.values(), ev.error_text]
    cause, matched = classify(texts)
    verdict, clause = decide(ev, cause)
    lines = wrapped(f"VERDICT: {verdict} {ev.data_dir.name}: {clause}", width, rest="    ")
    if verdict == "UNKNOWN":
        lines.append("next: check that this is a run folder: ls " + str(ev.data_dir))
        return verdict, lines
    if verdict == "OK":
        return verdict, lines + wrapped(f"data    {ev.data_dir}", width, rest="        ")
    shown = f"{cause} ({matched})" if matched else f"{cause}: no known pattern matched"
    lines += wrapped(f"cause: {shown}", width, rest="       ")
    if matched and cause != CAUSES[0].label:
        lines.append(GUESS_NOTE)
    lines += wrapped(next_step(ev, cause, hosts), width, rest="      ")
    if ev.failed:
        lines += ["", *failed_table(ev, width)]
    lines += ["", f"UCX / MPI / CUDA lines ({len(distinct)} distinct):" if distinct else "UCX / MPI / CUDA lines: none"]
    for count, text in distinct[:MAX_ROWS]:
        prefix = f"  x{count:<4}"
        lines += wrapped(text, width, prefix, " " * len(prefix), ERROR_LINES_PER_ROW)
    if len(distinct) > MAX_ROWS:
        more = f"{len(distinct) - MAX_ROWS}{'+' if len(distinct) >= MAX_DISTINCT else ''}"
        lines.append(f"  ... {more} more")
    last = tail_lines(ev)
    lines += ["", f"slurm_error.log, last {TAIL_LINES} lines:" if last else "slurm_error.log: no lines"]
    for text in last:
        lines += wrapped(text, width, "  ", "  ", ERROR_LINES_PER_ROW)
    return verdict, lines + ["", *wrapped(f"data    {ev.data_dir}", width, rest="        ")]


def existing_dir(path: Path, cfg: follow.Config) -> Path:
    """PATH as a folder, or the folder of that name under $DATA_ROOT (a relative run-log path)."""
    for candidate in (path, cfg.data_root / path.name):
        if candidate.is_dir():
            return candidate
    raise follow.FollowError(f"{path} is not a folder: nothing to read")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="diag_run.py", description="Why a run failed: one report from its run folder."
    )
    parser.add_argument("target", nargs="?", help="row number, job id, data dir or folder name")
    args = parser.parse_args(argv)
    try:
        cfg = follow.Config.from_env()
        target = follow.resolve_target(args.target, cfg)
        data_dir = existing_dir(target.data_dir, cfg)
    except (follow.FollowError, run_log.LogError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2
    verdict, lines = build_report(gather(data_dir), page_width())
    print(f"#report diag-run-{follow._safe(data_dir.name)}")
    print("\n".join(lines), flush=True)
    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
