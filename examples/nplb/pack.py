#!/usr/bin/env python3
"""`nplb pack`: everything the slot produced in one tarball, with a manifest and checksums.

    pack.py [--since TIME] [--out DIR] [--dry-run]

What goes in (regular files only, archived as):
    data/<run folder>/...   the run folders of $DATA_ROOT: every folder named by a `data_dir` of a run log
                            row, and every other folder whose newest mtime (the folder and its direct
                            files) is not older than --since
    work/...                the whole $WORK folder: run log, net state, node map, topology and probe
                            output, reports, and `smoke/` with its run log, reports and archive
    campaign/...            the whole $CAMPAIGN_DIR (schedule.tsv and the block files)
    reports/...             $NPLB_REPORT_DIR, only when it is set and sits outside $WORK
    pack_manifest.txt       `<sha256>  <size>  <path>` per file above (not for the two pack files)
    pack_info.json          created, since, crab_rev, blink_rev ({hash, dirty} or null), files, bytes, skipped
Every topology and probe output (topology_*.tsv, topo_probe.log, node_map.json) is written under $WORK,
so nothing else is read. The run logs are $WORK/campaign_runs.tsv, $WORK/smoke/campaign_runs.tsv and
$WORK/smoke/archive/*/campaign_runs.tsv. The default --since is the earliest `submitted` over them; with
no rows, or when one log cannot be read, there is no cut (since is null) and every folder is packed.
--since takes YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS]; it only decides about folders no run log row names.

Symlinks are never followed: they are skipped, counted and named. __pycache__, *.pyc and earlier
nplb-pack_* files are left out, and so is the pack folder when it sits inside a packed folder.
Sources are only read. The tar is streamed (no file is held in memory) and each sha256 is taken in the
same pass; a file that grows while it is read is cut at the size it had when its header was written.
The disk check wants free space at the out folder of at least the uncompressed bytes plus 5%. The
tarball is written as <name>.partial and renamed at the end; a failure removes it. Next to the tarball:
<name>.sha256 in `sha256sum -c` format.

Stderr is `[HH:MM:SS]` narration. Stdout is `#report pack` and the report (the verb pages and saves it):
the verdict first (OK, DRY RUN or FAIL), then path, size, file count, sha256, the CRAB and Blink
revisions and the lines that copy it out (placeholders only: nothing is ever copied from here).
Env: DATA_ROOT (default <checkout>/data/nplb_h100_gpu), WORK or NPLB_WORK, CAMPAIGN_DIR (default
<checkout>/local/campaign), NPLB_REPORT_DIR, PACK_DIR (default <checkout>/local/nplb-pack; --out wins),
PACK_FREE_BYTES (replaces the free-space reading, for tests), CRAB_DIR, BLINK_DIR / NPLB_LAB (as
net_state.py), NPLB_NOW, PAGE_COLS (default 100).
Exit 0 for OK and DRY RUN, 1 for FAIL, 2 for a usage error (message on stderr, nothing written).
Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402
import run_log  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
OWN_NAME = re.compile(r"^nplb-pack_.*\.tar\.gz(\.sha256|\.partial)?$")
SINCE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")
MANIFEST = "pack_manifest.txt"
INFO = "pack_info.json"
COMPRESS_LEVEL = 6
NARRATE_EVERY = 2000
MAX_LISTED = 5
PAGE_COLS = 100
LINE_BREAKS = re.compile(r"[\r\n]")


class UsageError(Exception):
    """A wrong flag value or folder: the message goes to stderr, the exit code is 2."""


@dataclass(frozen=True)
class Entry:
    src: Path
    arc: str
    size: int


@dataclass
class Selection:
    entries: list[Entry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name in the tarball, reason)
    folders: int = 0  # data run folders packed
    since: datetime | None = None
    since_why: str = ""
    warnings: list[str] = field(default_factory=list)
    log_unreadable: bool = False
    crab_rev: dict | None = None
    blink_rev: dict | None = None


@dataclass(frozen=True)
class Paths:
    data: Path
    work: Path
    campaign: Path
    reports: Path | None
    out: Path


@dataclass(frozen=True)
class Written:
    tarball: Path
    size: int
    sha256: str
    files: int
    bytes: int


def say(text: str) -> None:
    print(f"[{net_state.now().strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)


def page_width() -> int:
    try:
        return max(30, int(os.environ.get("PAGE_COLS") or PAGE_COLS))
    except ValueError:
        return PAGE_COLS


def parse_since(text: str) -> datetime:
    for fmt in SINCE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"{text!r} is not YYYY-MM-DD or YYYY-MM-DDTHH:MM")


def mib(count: int) -> str:
    return f"{count / 2**20:.1f} MiB"


def human(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    size = float(count)
    for unit in ("KiB", "MiB", "GiB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TiB"


def paths_from_env(out: str | None) -> Paths:
    data = os.environ.get("DATA_ROOT") or str(CHECKOUT / "data" / "nplb_h100_gpu")
    campaign = os.environ.get("CAMPAIGN_DIR") or str(CHECKOUT / "local" / "campaign")
    reports = os.environ.get("NPLB_REPORT_DIR")
    explicit = out or os.environ.get("PACK_DIR") or str(CHECKOUT / "local" / "nplb-pack")
    return Paths(
        Path(os.path.abspath(data)),
        Path(os.path.abspath(net_state.work_dir())),
        Path(os.path.abspath(campaign)),
        Path(os.path.abspath(reports)) if reports else None,
        Path(os.path.abspath(explicit)),
    )


# --- selection ------------------------------------------------------------------------------------


def inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def scan_tree(
    root: Path, prefix: str, pack_dir: Path, skipped: list[tuple[str, str]]
) -> list[Entry]:
    """The regular files under ROOT as `prefix/relative path`; symlinks and odd files go to SKIPPED."""
    entries: list[Entry] = []
    pack_real = pack_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel = here.relative_to(root)
        keep = []
        for name in sorted(dirnames):
            sub = here / name
            shown = "/".join([prefix, *rel.parts, name])
            if sub.is_symlink():
                skipped.append((shown, "symlink"))
                continue
            if name == "__pycache__" or sub.resolve() == pack_real:
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            path = here / name
            shown = "/".join([prefix, *rel.parts, name])
            if name.endswith(".pyc") or OWN_NAME.match(name):
                continue
            if LINE_BREAKS.search(name):
                skipped.append((shown, "newline in the name"))
                continue
            try:
                mode = path.lstat()
            except OSError:
                skipped.append((shown, "unreadable"))
                continue
            if stat.S_ISLNK(mode.st_mode):
                skipped.append((shown, "symlink"))
            elif not stat.S_ISREG(mode.st_mode):
                skipped.append((shown, "not a regular file"))
            else:
                entries.append(Entry(path, shown, mode.st_size))
    return entries


def newest_mtime(folder: Path) -> float:
    """The newest mtime of FOLDER and its direct children; now when it cannot be read (so: packed)."""
    try:
        newest = folder.stat().st_mtime
        with os.scandir(folder) as children:
            for child in children:
                newest = max(newest, child.stat(follow_symlinks=False).st_mtime)
    except OSError:
        return datetime.now().timestamp()
    return newest


def read_logs(work: Path) -> tuple[list[run_log.Run], list[str]]:
    """Every run log attempt, and one warning per log that could not be read."""
    smoke = work / "smoke"
    logs = [
        work / run_log.RUNS_FILE,
        smoke / run_log.RUNS_FILE,
        *sorted((smoke / "archive").glob(f"*/{run_log.RUNS_FILE}")),
    ]
    runs: list[run_log.Run] = []
    warnings: list[str] = []
    for log in logs:
        try:
            runs += run_log.read_runs(log)
        except run_log.LogError as exc:
            shown = "/".join(["work", *log.relative_to(work).parts])
            warnings.append(f"{shown} cannot be read ({exc}): nothing was cut by date")
    return runs, warnings


def earliest_submit(runs: list[run_log.Run]) -> datetime | None:
    times = []
    for run in runs:
        try:
            times.append(datetime.strptime(run.submitted, net_state.ISO))
        except ValueError:
            continue  # "-", empty or not a time
    return min(times) if times else None


def select(paths: Paths, since_flag: datetime | None) -> Selection:
    """Choose the files: the run folders, the work folder, the campaign folder, the reports folder."""
    sel = Selection()
    runs, sel.warnings = read_logs(paths.work)
    sel.log_unreadable = bool(sel.warnings)
    referenced = {Path(r.data_dir).name for r in runs if r.data_dir not in ("", "-")}
    if since_flag is not None:
        sel.since, sel.since_why = since_flag, "--since"
    elif not sel.log_unreadable:
        sel.since = earliest_submit(runs)
        sel.since_why = "earliest submit in the run logs"
    if paths.data.is_dir():
        for child in sorted(paths.data.iterdir()):
            shown = f"data/{child.name}"
            if child.is_symlink():
                sel.skipped.append((shown, "symlink"))
            elif not child.is_dir() or child.resolve() == paths.out.resolve():
                continue
            elif (
                child.name in referenced
                or sel.since is None
                or newest_mtime(child) >= sel.since.timestamp()
            ):
                sel.entries += scan_tree(child, shown, paths.out, sel.skipped)
                sel.folders += 1
            else:
                sel.skipped.append((shown, "older than since"))
    missing = sorted(n for n in referenced if not (paths.data / n).is_dir())
    if missing:
        sel.warnings.append(
            f"{len(missing)} run log folders are not under {paths.data}: " + ", ".join(missing[:3])
        )
    roots = [(paths.work, "work"), (paths.campaign, "campaign")]
    if paths.reports is not None:
        roots.append((paths.reports, "reports"))
    packed: list[Path] = []
    for root, prefix in roots:
        if not root.is_dir() or any(inside(root, other) for other in packed):
            continue
        sel.entries += scan_tree(root, prefix, paths.out, sel.skipped)
        packed.append(root)
    return sel


# --- writing --------------------------------------------------------------------------------------


class HashingWriter:
    """A write-only file object that hashes what passes through to RAW."""

    def __init__(self, raw: BinaryIO) -> None:
        self.raw = raw
        self.digest = hashlib.sha256()

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        return self.raw.write(data)

    def flush(self) -> None:
        self.raw.flush()


class HashingReader:
    """A read-only file object that hashes what is read from SOURCE."""

    def __init__(self, source: BinaryIO) -> None:
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        return data


def add_file(tar: tarfile.TarFile, entry: Entry) -> tuple[str, int]:
    """Add one file to TAR; (its sha256, the bytes archived). OSError when it got shorter meanwhile."""
    with entry.src.open("rb") as handle:
        info = tar.gettarinfo(arcname=entry.arc, fileobj=handle)
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        reader = HashingReader(handle)
        tar.addfile(info, reader)  # type: ignore[arg-type]
    return reader.digest.hexdigest(), info.size


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = int(net_state.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def free_bytes(folder: Path) -> int:
    fixed = os.environ.get("PACK_FREE_BYTES")
    if fixed:
        try:
            return int(fixed)
        except ValueError:
            raise UsageError(f"PACK_FREE_BYTES={fixed!r} is not a number") from None
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def needed_bytes(total: int) -> int:
    """TOTAL plus 5%, rounded up."""
    return -(-total * 105 // 100)


def tarball_name() -> str:
    return f"nplb-pack_{net_state.now().strftime('%Y%m%dT%H%M%S')}.tar.gz"


def free_name(folder: Path) -> str:
    name = tarball_name()
    count = 1
    while any((folder / (name + end)).exists() for end in ("", ".sha256", ".partial")):
        count += 1
        name = tarball_name().replace(".tar.gz", f"-{count}.tar.gz")
    return name


def info_dict(sel: Selection, files: int, total: int) -> dict:
    return {
        "created": net_state.now().strftime(net_state.ISO),
        "since": sel.since.strftime(net_state.ISO) if sel.since else None,
        "crab_rev": sel.crab_rev,
        "blink_rev": sel.blink_rev,
        "files": files,
        "bytes": total,
        "skipped": [{"path": path, "reason": why} for path, why in sel.skipped],
    }


def write_tarball(sel: Selection, folder: Path) -> Written:
    """Stream the selected files into FOLDER/<name>.partial, then rename it; remove it on any failure."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / free_name(folder)
    partial = final.with_name(final.name + ".partial")
    sidecar = final.with_name(final.name + ".sha256")
    done = False
    try:
        with partial.open("xb") as raw:
            hasher = HashingWriter(raw)
            gz = gzip.GzipFile(
                filename="", mode="wb", compresslevel=COMPRESS_LEVEL, fileobj=hasher, mtime=0
            )
            lines: list[str] = []
            total = 0
            try:
                with tarfile.open(fileobj=gz, mode="w|") as tar:
                    for index, entry in enumerate(sel.entries, 1):
                        sha, size = add_file(tar, entry)
                        lines.append(f"{sha}  {size}  {entry.arc}")
                        total += size
                        if index % NARRATE_EVERY == 0:
                            say(f"packed {index} of {len(sel.entries)} files ({mib(total)})")
                    add_bytes(tar, MANIFEST, ("\n".join(lines) + "\n").encode("utf-8"))
                    info = info_dict(sel, len(lines), total)
                    add_bytes(tar, INFO, (json.dumps(info, indent=2) + "\n").encode("utf-8"))
            finally:
                gz.close()  # while RAW is still open: a gzip left to the garbage collector warns
            raw.flush()
            os.fsync(raw.fileno())
        sidecar.write_text(f"{hasher.digest.hexdigest()}  {final.name}\n", encoding="utf-8")
        os.replace(partial, final)
        done = True
    finally:
        if not done:
            partial.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
    return Written(final, final.stat().st_size, hasher.digest.hexdigest(), len(lines), total)


# --- report ---------------------------------------------------------------------------------------


def wrapped(
    text: str, width: int, rest: str = "", max_lines: int = 0, long_words: bool = True
) -> list[str]:
    """TEXT wrapped to WIDTH columns; at most MAX_LINES lines (0: all), the last one ending in "~"."""
    lines = textwrap.wrap(
        text,
        width=width,
        subsequent_indent=rest,
        break_long_words=long_words,
        break_on_hyphens=False,
    )
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1] + "~"
    return lines or [""]


def listed(items: list[str], width: int, indent: str, max_lines: int = 1) -> list[str]:
    lines: list[str] = []
    for item in items[:MAX_LISTED]:
        lines += wrapped(indent + item, width, indent + "  ", max_lines)
    if len(items) > MAX_LISTED:
        lines.append(f"{indent}... {len(items) - MAX_LISTED} more")
    return lines


def rev_text(rev: dict | None, where: Path) -> str:
    if rev is None:
        return f"none: {where} not found"
    dirty = {True: " (dirty)", False: " (clean)", None: ""}[rev["dirty"]]
    return f"{rev['hash']}{dirty}"


def skipped_lines(sel: Selection, width: int) -> list[str]:
    if not sel.skipped:
        return ["skipped  none"]
    reasons = Counter(why for _, why in sel.skipped)
    head = ", ".join(f"{count} {why}" for why, count in reasons.items())
    lines = wrapped(f"skipped  {len(sel.skipped)} ({head})", width, "         ", 2)
    return lines + listed([f"{path}  {why}" for path, why in sel.skipped], width, "  ")


def body(sel: Selection, paths: Paths, width: int) -> list[str]:
    since = sel.since.strftime(net_state.ISO) if sel.since else None
    if since:
        since_line = f"since    {since} ({sel.since_why})"
    elif sel.log_unreadable:
        since_line = "since    none (a run log could not be read: everything is packed)"
    else:
        since_line = "since    none (no run log rows: everything is packed)"
    lines = [since_line]
    lines += [
        "crab     " + rev_text(sel.crab_rev, Path(os.environ.get("CRAB_DIR") or CHECKOUT)),
        "blink    " + rev_text(sel.blink_rev, net_state.blink_dir()),
    ]
    lines += wrapped(f"data     {sel.folders} folders under {paths.data}", width, "         ", 2)
    lines += wrapped(f"work     {paths.work}", width, "         ", 2)
    lines += wrapped(f"campaign {paths.campaign}", width, "         ", 2)
    lines += skipped_lines(sel, width)
    if sel.warnings:
        lines.append("warnings")
        lines += listed(sel.warnings, width, "  ", 2)
    return lines


def fail_report(text: str, width: int, *more: str) -> list[str]:
    """The one-line verdict, then MORE: paths stay whole (the pager cuts a line that is too wide)."""
    lines = [f"VERDICT: FAIL pack: {text}"]
    for line in more:
        lines += wrapped(line, width, "         ", long_words=False)
    return lines


def dry_report(sel: Selection, paths: Paths, width: int, total: int, free: int) -> list[str]:
    files = len(sel.entries)
    lines = [
        f"VERDICT: DRY RUN pack: {files} files, {mib(total)} would be packed",
        f"out      {paths.out}",
        f"files    {files}",
        f"bytes    {total} uncompressed",
        f"free     {free} bytes at {paths.out} (needs {needed_bytes(total)})",
    ]
    return (
        lines
        + body(sel, paths, width)
        + ["", "nothing was written: run it again without --dry-run"]
    )


def ok_report(sel: Selection, paths: Paths, width: int, written: Written) -> list[str]:
    name = written.tarball.name
    lines = [
        f"VERDICT: OK pack: {written.files} files, {mib(written.bytes)} -> {name} ({mib(written.size)})",
        f"path     {written.tarball}",
        f"size     {written.size} bytes",
        f"files    {written.files}",
        f"bytes    {written.bytes} uncompressed",
        f"sha256   {written.sha256}",
    ]
    lines += body(sel, paths, width)
    where = f"<lab-host>:{written.tarball}"
    return lines + [
        "",
        "copy it out, run on the laptop (nothing was copied from here):",
        f"  scp {where} .",
        f"  scp {where}.sha256 .",
        f"  sha256sum -c {name}.sha256",
    ]


# --- main -----------------------------------------------------------------------------------------


def emit(lines: list[str]) -> None:
    print("#report pack")
    print("\n".join(lines), flush=True)


def run(args: argparse.Namespace) -> int:
    paths = paths_from_env(args.out)
    if paths.out.exists() and not paths.out.is_dir():
        raise UsageError(f"{paths.out} is not a folder: --out must be a directory")
    width = page_width()
    say(f"reading the run logs under {paths.work}")
    say(f"choosing the files (data {paths.data}, work {paths.work}, campaign {paths.campaign})")
    sel = select(paths, args.since)
    sel.crab_rev = net_state.git_rev(Path(os.environ.get("CRAB_DIR") or CHECKOUT))
    sel.blink_rev = net_state.git_rev(net_state.blink_dir())
    if not sel.entries:
        checked = f"{paths.data}, {paths.work}, {paths.campaign}"
        say("nothing to pack")
        emit(
            fail_report(
                f"nothing to pack (checked {checked})",
                width,
                f"data     {paths.data}",
                f"work     {paths.work}",
                f"campaign {paths.campaign}",
            )
        )
        return 1
    total = sum(entry.size for entry in sel.entries)
    free = free_bytes(paths.out)
    need = needed_bytes(total)
    say(f"{len(sel.entries)} files, {mib(total)} uncompressed, {len(sel.skipped)} skipped")
    if free < need:
        say(f"not enough free space at {paths.out}")
        emit(
            fail_report(
                f"need {human(need)}, free {human(free)} at {paths.out}",
                width,
                "next: nplb pack --out DIR on a bigger disk, or narrow it with --since",
            )
        )
        return 1
    if args.dry_run:
        emit(dry_report(sel, paths, width, total, free))
        return 0
    say(f"writing the tarball into {paths.out}")
    try:
        written = write_tarball(sel, paths.out)
    except OSError as exc:
        say("the write failed, the partial file was removed")
        emit(fail_report(f"cannot write the tarball into {paths.out}: {exc}", width))
        return 1
    say(f"done: {written.tarball}")
    emit(ok_report(sel, paths, width, written))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pack.py", description="One tarball of the slot data, with a manifest and sha256."
    )
    parser.add_argument(
        "--since",
        type=parse_since,
        help="YYYY-MM-DD or YYYY-MM-DDTHH:MM: oldest unreferenced folder",
    )
    parser.add_argument("--out", help="the folder for the tarball (default $PACK_DIR)")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (UsageError, net_state.Fail) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
