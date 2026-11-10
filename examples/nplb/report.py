#!/usr/bin/env python3
"""The lab analysis: `nplb report` and `nplb compare`, read only, stdlib only.

    report.py report [N | --state LABEL | --victim V | --layout L | --all] [--run DIR ...]
    report.py compare [VICTIM] [CONDITION] [--layout L] [--run DIR ...]

`report` prints slowdown tables for the finished runs of the campaign: page 1 is a summary (the
verdict first), then one table per state, layout and victim (rows = victim message sizes, columns =
`alone ms` and the conditions, cell = median time / median of the `alone` run of the same state,
layout, victim and size). With no argument the scope is the state of the newest finished run of the
run log; `N` is the runs of schedule row N, `--state` a state (nplb1_ar0_sr1 or n1a0s1), `--all`
every state; `--victim` and `--layout` alone take every state and narrow it. `compare` takes one
victim (default a2a), one condition (default steady_a128k; a campaign name or its code) and a layout
(default split-i) and shows rows = sizes, columns = the 8 network states. The metrics are defined in
report_data.py, the page layout in report_tables.py.

Runs come from `campaign_runs.tsv` in $WORK (else $NPLB_WORK, default <checkout>/local/nplb-work):
the attempts with status OK, WARN or FAIL and a data dir; a relative data dir is looked up under
$DATA_ROOT (default <checkout>/data/nplb_h100_gpu), as is a moved folder by its name. `--run DIR`
(repeatable) reads a bare run folder instead of the log, as the anonymous state `unknown`, layout and
victim guessed from the folder name or `?`. The report goes to stdout in plain text with a form feed
line between the pages ($PAGE_LINES, $PAGE_COLS, default 38 x 100); the verb pages it. Exit 0, 2 on
a usage error, an unknown name, a bad run log or a missing --run folder (message on stderr). Nothing
is written. Python 3.10.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net_state  # noqa: E402
import report_data as data  # noqa: E402
import report_tables as tables  # noqa: E402
import run_log  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[2]
DEFAULT_LINES = 38
DEFAULT_COLS = 100
FRAME_LINES = 2  # the pager's header and footer
DEFAULT_VICTIM = "a2a"
DEFAULT_CONDITION = "steady_a128k"
DEFAULT_LAYOUT = "split-i"
READ_STATUSES = run_log.TIMED  # OK, WARN, FAIL


class UsageError(Exception):
    """A bad argument or input: the message goes to stderr and the exit code is 2."""


def env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(int(os.environ.get(name) or default), minimum)
    except ValueError:
        return default


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT") or CHECKOUT / "data" / "nplb_h100_gpu")


def valid_text(what: str, valid: list[str]) -> str:
    return f"unknown {what}: use {' '.join(valid)}"


# --- reading the runs ----------------------------------------------------------------------------


def find_dir(text: str) -> Path | None:
    """The data dir of a log line: as written, else under $DATA_ROOT (also a moved folder by name)."""
    path = Path(text)
    root = data_root()
    for candidate in (path, root / path, root / path.name):
        if candidate.is_dir():
            return candidate
    return None


def candidates(dirs: list[str]) -> tuple[list[tuple[data.Meta, Path]], list[str]]:
    """The run folders to read and the problems met on the way (missing folders)."""
    found: list[tuple[data.Meta, Path]] = []
    problems: list[str] = []
    if dirs:
        for index, text in enumerate(dirs):
            path = Path(text)
            if not path.is_dir():
                raise UsageError(f"no such run folder: {text}")
            found.append((data.Meta(None, "", "", "", "", "OK", index), path))
        return found, problems
    try:
        runs = run_log.read_runs(run_log.runs_path())
    except (run_log.LogError, net_state.Fail) as exc:
        raise UsageError(str(exc)) from None
    missing: list[str] = []
    for index, run in enumerate(runs):
        if run.status not in READ_STATUSES or run.data_dir in ("", "-"):
            continue
        path = find_dir(run.data_dir)
        if path is None:
            missing.append(f"row {run.n}")
            continue
        meta = data.Meta(run.n, run.state, run.layout, run.victim, run.submitted, run.status, index)
        found.append((meta, path))
    if missing:
        shown = ", ".join(missing[:4]) + (" ..." if len(missing) > 4 else "")
        noun = "run folder" if len(missing) == 1 else "run folders"
        problems.append(f"{len(missing)} {noun} missing, not read ({shown})")
    return found, problems


def inspect_all(found: list[tuple[data.Meta, Path]]) -> tuple[list[data.RunInfo], list[str]]:
    infos: list[data.RunInfo] = []
    problems: list[str] = []
    for meta, path in found:
        try:
            infos.append(data.inspect_run(path, meta))
        except data.RunError as exc:
            problems.append(f"skipped {exc}")
    return infos, problems


# --- scopes --------------------------------------------------------------------------------------


def newest(infos: list[data.RunInfo]) -> data.RunInfo:
    finished = [i for i in infos if i.meta.status in run_log.FINISHED] or infos
    return max(finished, key=lambda i: (i.meta.submitted, i.meta.order))


def check_name(what: str, value: str, known: tuple[str, ...], infos: list[data.RunInfo]) -> None:
    seen = {getattr(i, what) for i in infos} - {"?"}
    valid = [*known, *sorted(seen - set(known))]
    if value not in valid:
        raise UsageError(valid_text(what, valid))


def select_report(
    infos: list[data.RunInfo], args: argparse.Namespace, from_dirs: bool
) -> tuple[list[data.RunInfo], str]:
    """The runs of the scope and its description."""
    given = [f"victim {args.victim}"] if args.victim else []
    given += [f"layout {args.layout}"] if args.layout else []
    if args.victim:
        check_name("victim", args.victim, data.VICTIMS, infos)
    if args.layout:
        check_name("layout", args.layout, data.LAYOUTS, infos)
    if args.row is not None:
        try:
            row = int(args.row)
        except ValueError:
            raise UsageError(f"N must be a row number of the schedule, not {args.row!r}") from None
        chosen = [i for i in infos if i.meta.n == row]
        if not chosen:
            rows = sorted({i.meta.n for i in infos if i.meta.n is not None})
            raise UsageError(
                f"row {row} has no finished run; rows with runs: {' '.join(map(str, rows)) or 'none'}"
            )
        what = [f"row {row}"]
    elif args.state:
        label = data.state_from_text(args.state)
        valid = [*data.STATE_LABELS, *sorted({i.state for i in infos} - set(data.STATE_LABELS))]
        if label not in valid:
            raise UsageError(valid_text("state", valid))
        chosen = [i for i in infos if i.state == label]
        what = [f"state {label}"]
    elif args.all or given or from_dirs or not infos:
        chosen = list(infos)
        what = ["all states"]
    else:
        label = newest(infos).state
        chosen = [i for i in infos if i.state == label]
        what = [f"state {label} (the newest finished run)"]
    chosen = [i for i in chosen if (not args.victim or i.victim == args.victim)]
    chosen = [i for i in chosen if (not args.layout or i.layout == args.layout)]
    return chosen, ", ".join(what + given)


# --- verbs ---------------------------------------------------------------------------------------


def counts_text(infos: list[data.RunInfo]) -> str:
    return tables.plural(len(infos), "run folder") + " read"


def cmd_report(args: argparse.Namespace) -> list[str]:
    if args.all and (args.row is not None or args.state):
        raise UsageError("--all cannot be combined with N or --state")
    found, problems = candidates(args.run)
    infos, more = inspect_all(found)
    problems += more
    if not infos:
        return ["VERDICT: WARN no finished runs in the run log"] + problems[:3]
    chosen, scope = select_report(infos, args, bool(args.run))
    if not chosen:
        return [f"VERDICT: WARN no finished runs in scope ({scope})"] + problems[:3]
    cells = data.build_cells([data.load_points(i) for i in chosen])
    analysis = data.analyse(cells)
    group_list = data.groups(cells)
    states = list(dict.fromkeys(g.state for g in group_list))
    in_scope = {i.state for i in chosen}
    others: dict[str, int] = {}
    for info in infos:
        if info.state not in in_scope:
            others[info.state] = others.get(info.state, 0) + 1
    ordered = sorted(others, key=lambda s: data.order_of(s, data.STATE_LABELS))
    width = env_int("PAGE_COLS", DEFAULT_COLS, 30)
    capacity = env_int("PAGE_LINES", DEFAULT_LINES, 5) - FRAME_LINES
    first = tables.summary_lines(
        f"{scope}; {counts_text(chosen)}",
        states,
        cells,
        analysis,
        [(s, others[s]) for s in ordered],
        problems,
    )
    blocks = [tables.group_table(g, cells, analysis, width) for g in group_list]
    return tables.paginate(first, blocks, capacity)


def parse_compare_names(words: list[str]) -> tuple[str, str]:
    victim = condition = ""
    for word in words:
        by_code = [name for name, code in data.CODES.items() if code == word]
        if word in data.VICTIMS and not victim:
            victim = word
        elif (word in data.CODES or by_code) and not condition:
            condition = word if word in data.CODES else by_code[0]
        else:
            valid = valid_text("victim", list(data.VICTIMS))
            raise UsageError(f"{word!r}: {valid}\n{valid_text('condition', list(data.CODES))}")
    return victim or DEFAULT_VICTIM, condition or DEFAULT_CONDITION


def cmd_compare(args: argparse.Namespace) -> list[str]:
    victim, condition = parse_compare_names(args.names)
    found, problems = candidates(args.run)
    infos, _more = inspect_all(found)
    layout = args.layout or DEFAULT_LAYOUT
    check_name("layout", layout, data.LAYOUTS, infos)
    chosen = [i for i in infos if i.victim == victim and i.layout == layout]
    only = {condition, "alone"}
    cells = data.build_cells([data.load_points(i, only) for i in chosen])
    states = list(dict.fromkeys(i.state for i in chosen))
    width = env_int("PAGE_COLS", DEFAULT_COLS, 30)
    return tables.compare_lines(victim, condition, layout, cells, states, width) + problems[:2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NPLB lab analysis: slowdown tables and compare.")
    sub = parser.add_subparsers(dest="verb", required=True)
    rep = sub.add_parser("report", help="slowdown tables per victim")
    rep.add_argument("row", nargs="?", metavar="N", help="a schedule row number")
    rep.add_argument("--state", help="a network state: nplb1_ar0_sr1 or n1a0s1")
    rep.add_argument("--victim", help="a2a, agtr or ardc (all states unless narrowed)")
    rep.add_argument("--layout", help="split-i, split-l or shared (all states unless narrowed)")
    rep.add_argument("--all", action="store_true", help="every state of the run log")
    cmp = sub.add_parser("compare", help="one victim and condition across the network states")
    cmp.add_argument("names", nargs="*", metavar="VICTIM|CONDITION", help="in either order")
    cmp.add_argument("--layout", help=f"default {DEFAULT_LAYOUT}")
    for p in (rep, cmp):
        p.add_argument("--run", action="append", default=[], metavar="DIR", help="a bare run folder")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lines = cmd_report(args) if args.verb == "report" else cmd_compare(args)
    except UsageError as exc:
        print(exc, file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
