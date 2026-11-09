#!/usr/bin/env python3
"""The NPLB report pager: numbered pages of at most 38 lines x 100 columns.

    pages.py emit NAME [--file FILE]    save the report (stdin or FILE), print page 1
    pages.py page [N|next|prev] [REPORT]  replay a page (default: page 1 of the newest report)
    pages.py reports                    list the saved reports, newest first

The whole report is saved as <reports dir>/<YYYYmmddTHHMMSS>_<NAME>.txt and `latest` points to
it; nothing is ever cut on disk. The screen gets a header, at most PAGE_LINES-2 content lines and
a footer that names the next command. A line wider than PAGE_COLS ends in "~"; ANSI escapes are
dropped; a line that is exactly a form feed starts a new page. The last page shown is remembered
in <reports dir>/.cursor so `page next` and `page prev` walk the report.

Reports dir: $NPLB_REPORT_DIR, else ${NPLB_WORK:-<checkout>/local/nplb-work}/reports.
Size: $PAGE_LINES (default 38, at least 5) and $PAGE_COLS (default 100, at least 30).
$NPLB_NOW (YYYY-MM-DDTHH:MM:SS) replaces the clock, for tests. Exit 0 on success, 2 on a usage
error, an unknown report or a page out of range (messages on stderr). Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_LINES = 38
DEFAULT_COLS = 100
MIN_LINES = 5
MIN_COLS = 30
FRAME_LINES = 2  # header and footer
EMPTY_REPORT = "(empty report)"
STAMP = "%Y%m%dT%H%M%S"
FILE_RE = re.compile(r"^(\d{8}T\d{6})_(.+)\.txt$")
SUFFIX_RE = re.compile(r"^(.+)-(\d+)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
CHECKOUT = Path(__file__).resolve().parents[2]


class Fail(Exception):
    """A user-facing error: the message goes to stderr and the exit code is 2."""


@dataclass(frozen=True)
class Geometry:
    lines: int
    cols: int

    @property
    def content(self) -> int:
        return self.lines - FRAME_LINES


@dataclass(frozen=True)
class Report:
    path: Path
    stamp: str  # YYYYmmddTHHMMSS
    name: str  # without the .txt and without a collision suffix
    seq: int  # 1, or the collision suffix (-2, -3, ...)

    @property
    def when(self) -> datetime:
        return datetime.strptime(self.stamp, STAMP)


# ---- pure logic -------------------------------------------------------------------------------


def fit(line: str, cols: int) -> str:
    """LINE without trailing blanks, cut to COLS characters with a final "~" when too wide."""
    line = line.rstrip()
    if len(line) > cols:
        return line[: cols - 1] + "~"
    return line


def clean_line(line: str, cols: int) -> str:
    return fit(ANSI_RE.sub("", line).replace("\x1b", "").expandtabs(8), cols)


def build_pages(raw: bytes, geo: Geometry) -> list[list[str]]:
    """Split a report into pages of content lines (never empty: an empty report is one page)."""
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    pages: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        line = line.rstrip("\r")
        if line == "\f":
            if current:
                pages.append(current)
                current = []
            continue
        current.append(clean_line(line, geo.cols))
        if len(current) == geo.content:
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    return pages or [[EMPTY_REPORT]]


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def render(report: Report, pages: list[list[str]], k: int, geo: Geometry) -> list[str]:
    total = len(pages)
    header = f"{report.name} | page {k}/{total} | {report.when:%H:%M}"
    if k < total:
        footer = f"-- next: nplb page {k + 1} (of {total}) --"
    else:
        footer = f"-- end of {report.name} ({plural(total, 'page')}) --"
    return [fit(header, geo.cols), *pages[k - 1], fit(footer, geo.cols)]


def parse_name(basename: str) -> Report | None:
    match = FILE_RE.match(basename)
    if not match:
        return None
    stem = match.group(2)
    seq = 1
    suffix = SUFFIX_RE.match(stem)
    if suffix and int(suffix.group(2)) >= 2:
        stem, seq = suffix.group(1), int(suffix.group(2))
    return Report(Path(basename), match.group(1), stem, seq)


def pick_report(reports: list[Report], wanted: str | None) -> Report | None:
    """REPORTS is newest first. WANTED matches the name exactly, else as a prefix, else the
    whole file name."""
    if wanted is None:
        return reports[0] if reports else None
    for test in (
        lambda r: r.name == wanted,
        lambda r: r.name.startswith(wanted),
        lambda r: wanted in (r.path.name, r.path.name.removesuffix(".txt")),
    ):
        for report in reports:
            if test(report):
                return report
    return None


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


# ---- environment and files --------------------------------------------------------------------


def geometry_from_env() -> Geometry:
    values = []
    for var, default, minimum in (
        ("PAGE_LINES", DEFAULT_LINES, MIN_LINES),
        ("PAGE_COLS", DEFAULT_COLS, MIN_COLS),
    ):
        text = os.environ.get(var, "")
        try:
            value = int(text) if text else default
        except ValueError:
            raise Fail(f"{var}={text!r} is not a number") from None
        if value < minimum:
            raise Fail(f"{var}={value} is too small, the minimum is {minimum}")
        values.append(value)
    return Geometry(values[0], values[1])


def reports_dir() -> Path:
    explicit = os.environ.get("NPLB_REPORT_DIR")
    if explicit:
        return Path(explicit)
    work = os.environ.get("NPLB_WORK")
    return (Path(work) if work else CHECKOUT / "local" / "nplb-work") / "reports"


def now_stamp() -> str:
    fixed = os.environ.get("NPLB_NOW")
    if not fixed:
        return datetime.now().strftime(STAMP)
    try:
        return datetime.strptime(fixed, "%Y-%m-%dT%H:%M:%S").strftime(STAMP)
    except ValueError:
        raise Fail(f"NPLB_NOW={fixed!r} is not YYYY-MM-DDTHH:MM:SS") from None


def list_reports(directory: Path) -> list[Report]:
    """The saved reports, newest first."""
    found: list[Report] = []
    if directory.is_dir():
        for entry in directory.iterdir():
            report = parse_name(entry.name)
            if report is None or not entry.is_file():
                continue
            try:
                datetime.strptime(report.stamp, STAMP)
            except ValueError:
                continue
            found.append(Report(entry, report.stamp, report.name, report.seq))
    found.sort(key=lambda r: (r.stamp, r.seq, r.path.name), reverse=True)
    return found


def save_report(directory: Path, name: str, raw: bytes) -> Report:
    """Write RAW to a fresh file (never overwriting) and point `latest` at it."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()
    seq = 1
    while True:
        suffix = "" if seq == 1 else f"-{seq}"
        path = directory / f"{stamp}_{name}{suffix}.txt"
        try:
            with open(path, "xb") as handle:
                handle.write(raw)
            break
        except FileExistsError:
            seq += 1
    link = directory / ".latest.tmp"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(path.name)
    os.replace(link, directory / "latest")
    return Report(path, stamp, name, seq)


def read_cursor(directory: Path) -> tuple[str, int] | None:
    try:
        basename, number = (directory / ".cursor").read_text().strip().split("\t")
        return basename, int(number)
    except (OSError, ValueError):
        return None


def write_cursor(directory: Path, report: Report, page: int) -> None:
    (directory / ".cursor").write_text(f"{report.path.name}\t{page}\n")


# ---- commands ---------------------------------------------------------------------------------


def show(directory: Path, report: Report, k: int, geo: Geometry) -> int:
    pages = build_pages(report.path.read_bytes(), geo)
    if not 1 <= k <= len(pages):
        raise Fail(f"page {k} does not exist, the report has {plural(len(pages), 'page')}")
    sys.stdout.write("\n".join(render(report, pages, k, geo)) + "\n")
    write_cursor(directory, report, k)
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    geo = geometry_from_env()
    if args.file:
        try:
            raw = Path(args.file).read_bytes()
        except OSError as err:
            raise Fail(f"cannot read {args.file}: {err.strerror}") from None
    else:
        raw = sys.stdin.buffer.read()
    directory = reports_dir()
    report = save_report(directory, safe_name(args.name), raw)
    return show(directory, report, 1, geo)


def parse_page_args(words: list[str]) -> tuple[str, str | None]:
    """(page, report): page is a number, "next" or "prev"; a first word that is none of these
    is the report name (page 1)."""
    if len(words) > 2:
        raise Fail("usage: nplb page [N|next|prev] [REPORT]")
    if not words:
        return "1", None
    first = words[0]
    if first in ("next", "prev") or first.isdigit():
        return first, (words[1] if len(words) == 2 else None)
    if len(words) == 2:
        raise Fail("usage: nplb page [N|next|prev] [REPORT]")
    return "1", first


def cmd_page(args: argparse.Namespace) -> int:
    geo = geometry_from_env()
    which, wanted = parse_page_args(args.words)
    directory = reports_dir()
    reports = list_reports(directory)
    if not reports:
        raise Fail(f"no reports yet in {directory}")
    cursor = read_cursor(directory)
    if wanted is None and which in ("next", "prev") and cursor:
        wanted = next((r.path.name for r in reports if r.path.name == cursor[0]), None)
    report = pick_report(reports, wanted)
    if report is None:
        raise Fail(f"no report matches '{wanted}' in {directory}, run: nplb reports")
    if which.isdigit():
        k = int(which)
    else:
        total = len(build_pages(report.path.read_bytes(), geo))
        base = cursor[1] if cursor and cursor[0] == report.path.name else 1
        k = max(1, min(total, base + (1 if which == "next" else -1)))
    return show(directory, report, k, geo)


def cmd_reports(_args: argparse.Namespace) -> int:
    geo = geometry_from_env()
    directory = reports_dir()
    reports = list_reports(directory)
    if not reports:
        print(f"no reports yet in {directory}")
        return 0
    shown = reports if len(reports) <= geo.lines else reports[: geo.lines - 1]
    out = []
    for number, report in enumerate(shown, start=1):
        count = len(build_pages(report.path.read_bytes(), geo))
        line = f"{number}  {report.when:%Y-%m-%d %H:%M}  {report.name}  {plural(count, 'page')}"
        out.append(fit(line, geo.cols))
    if len(shown) < len(reports):
        out.append(fit(f"... {len(reports) - len(shown)} older reports in {directory}", geo.cols))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pages.py", description="NPLB report pager")
    sub = parser.add_subparsers(dest="verb", required=True)
    emit = sub.add_parser("emit", help="save a report and print page 1")
    emit.add_argument("name")
    emit.add_argument("--file", help="read the report from FILE instead of stdin")
    emit.set_defaults(run=cmd_emit)
    page = sub.add_parser("page", help="replay a page")
    page.add_argument("words", nargs="*", metavar="[N|next|prev] [REPORT]")
    page.set_defaults(run=cmd_page)
    reports = sub.add_parser("reports", help="list the saved reports")
    reports.set_defaults(run=cmd_reports)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    args = build_parser().parse_args(argv)
    try:
        return int(args.run(args))
    except Fail as err:
        print(err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
