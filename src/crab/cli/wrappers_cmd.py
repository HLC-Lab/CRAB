"""`crab parse`, `crab wrappers list|test|new` and `crab receipts set`.

`crab parse` runs a wrapper's parser on saved output through the same checks a real run uses
(core/data/parse.py), so a sample test, SbatchMan's parser and a CRAB run all read an output the
same way. `crab receipts set` records where a binary lives without the interactive wizard.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Any

_RECEIPT_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _fail(message: str, code: int = 2) -> None:
    print(f"crab: {message}", file=sys.stderr)
    sys.exit(code)


def _setting(raw: str) -> tuple[str, Any]:
    """`key=value` from --set; the value is read as JSON when it parses, else kept as text."""
    if "=" not in raw:
        _fail(f"--set expects key=value, got {raw!r}")
    key, value = raw.split("=", 1)
    try:
        return key, json.loads(value)
    except json.JSONDecodeError:
        return key, value


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as exc:
        _fail(f"cannot read {path}: {exc.strerror}")
    raise AssertionError("unreachable")


def handle_parse(args: argparse.Namespace) -> None:
    from crab.cli.wrappers_catalog import parse_saved_output
    from crab.core.data.parse import ParseError
    from crab.core.wrapper_paths import resolve_wrapper_path

    path = resolve_wrapper_path(args.wrapper)
    if not os.path.isfile(path):
        _fail(f"wrapper not found: {path}")

    settings = dict(_setting(raw) for raw in args.set or [])
    stderr = _read(args.stderr) if args.stderr else b""
    run_dir = args.dir or os.path.dirname(args.output) or "."
    try:
        app, parsed = parse_saved_output(
            path, _read(args.output), stderr, run_dir, args.args, settings
        )
    except ParseError as exc:
        _fail(str(exc))
    if parsed.legacy:
        print("crab: note: this wrapper returns the old list-per-metric shape", file=sys.stderr)

    if args.check:
        from crab.cli.wrappers_catalog import row_differences

        with open(args.check) as f:
            expected = json.load(f)
        diffs = row_differences(parsed.rows, expected)
        if diffs:
            for line in diffs:
                print(line, file=sys.stderr)
            sys.exit(1)
        print(f"ok: {len(parsed.rows)} rows match {args.check}")
        return

    if args.json:
        print(json.dumps({"rows": parsed.rows, "legacy_shape": parsed.legacy}, indent=2))
        return
    fields = list(getattr(app, "keys", []) or []) + [m["name"] for m in app.metadata]
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(parsed.rows)


def handle_receipts_set(args: argparse.Namespace) -> None:
    import crab.setup.memory as memory
    from crab.cli.contract import CONTRACT_SCHEMA

    if not _RECEIPT_ID.match(args.id):
        _fail(f"receipt id {args.id!r} may only use letters, digits, '_', '-' and '.'")
    if not args.allow_missing and not os.path.exists(args.binary):
        _fail(f"binary {args.binary} does not exist (use --allow-missing to record it anyway)")

    receipt = {
        "id": args.id,
        "type": "binary",
        "binary_path": args.binary,
        "launcher_override": args.launcher or "",
        "hooks": {"pre_run": list(args.pre_run or []), "post_run": []},
    }
    memory.save_receipt(args.id, receipt)
    if args.json:
        print(json.dumps({"schema": CONTRACT_SCHEMA, "receipt": receipt}, indent=2))
    else:
        print(f"Saved receipt {args.id!r}: {args.binary}")


def handle_wrappers_list(args: argparse.Namespace) -> None:
    from crab.cli import contract
    from crab.cli.wrappers_catalog import gather_wrappers

    def human(data: dict[str, Any]) -> None:
        print("Search path: " + " > ".join(data["search_path"]))
        for w in data["wrappers"]:
            if not w["loadable"]:
                print(f"  {w['relpath']:<40} cannot load: {w['error']}")
                continue
            binary = w["binary"]
            where = f"{binary['status']}: {binary['path']}" if binary["path"] else binary["status"]
            print(f"  {w['relpath']:<40} binary {where}")

    contract.emit(gather_wrappers(), args.json, human)


def handle_wrappers_test(args: argparse.Namespace) -> None:
    from crab.cli.wrappers_catalog import run_samples

    try:
        results = run_samples(args.apps or None, args.strict)
    except LookupError as exc:
        _fail(str(exc))
    for r in results:
        print(f"{r.status}  {r.label}" + (f": {r.detail}" if r.detail else ""))
    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)} ok, {len(failed)} failed")
    if failed:
        sys.exit(1)


def handle_wrappers_new(args: argparse.Namespace) -> None:
    from pathlib import Path

    from crab.cli.wrappers_catalog import scaffold
    from crab.core.wrapper_paths import wrapper_search_path

    search = wrapper_search_path()
    target = Path(args.dir or (search[0] if args.local else search[1]))
    try:
        wrapper = scaffold(args.app, args.name, target)
    except ValueError as exc:
        _fail(str(exc))
    print(f"Created {wrapper}")
    print(f"Edit it, replace samples/ with real output, then run: crab wrappers test {args.app}")


def register(subparsers: Any) -> None:
    """Add the `parse` and `receipts` commands to the main CLI."""
    parser_parse = subparsers.add_parser(
        "parse", help="Parse saved output with a wrapper, as a run would"
    )
    parser_parse.add_argument(
        "wrapper", help="Wrapper file (relative paths use CRAB_PATH_WRAPPERS)."
    )
    parser_parse.add_argument("output", help="File holding the application's stdout.")
    parser_parse.add_argument("--stderr", default=None, help="File holding its stderr.")
    parser_parse.add_argument(
        "--dir",
        default=None,
        help="Run directory with the files it wrote (default: output's folder).",
    )
    parser_parse.add_argument("--args", default="", help="The app's args string from the config.")
    parser_parse.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help="An extra app config key (repeatable)."
    )
    parser_parse.add_argument("--json", action="store_true", help="Print rows as JSON.")
    parser_parse.add_argument(
        "--check",
        default=None,
        metavar="EXPECTED_JSON",
        help="Compare rows with a JSON list; exit 1 on a difference.",
    )
    parser_parse.set_defaults(func=handle_parse)

    parser_wrappers = subparsers.add_parser("wrappers", help="List, test and create wrappers")
    wrappers_sub = parser_wrappers.add_subparsers(dest="wrappers_command", required=True)
    parser_list = wrappers_sub.add_parser(
        "list", help="Wrappers on the search path and their binaries"
    )
    parser_list.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser_list.set_defaults(func=handle_wrappers_list)
    parser_test = wrappers_sub.add_parser("test", help="Run the sample cases of each app")
    parser_test.add_argument("apps", nargs="*", help="App folders to test (default: all).")
    parser_test.add_argument(
        "--strict",
        action="store_true",
        help="Fail on wrappers with no sample unless listed in unverified.txt.",
    )
    parser_test.set_defaults(func=handle_wrappers_test)
    parser_new = wrappers_sub.add_parser(
        "new", help="Create a wrapper with a passing example sample"
    )
    parser_new.add_argument("app", help="App folder name, e.g. hpl.")
    parser_new.add_argument(
        "--name", default=None, help="Wrapper file name (default: the app name)."
    )
    parser_new.add_argument(
        "--local", action="store_true", help="Create it in local/wrappers (private, untracked)."
    )
    parser_new.add_argument(
        "--dir", default=None, help="Create it in this wrappers folder instead."
    )
    parser_new.set_defaults(func=handle_wrappers_new)

    parser_receipts = subparsers.add_parser("receipts", help="Manage benchmark receipts")
    receipts_sub = parser_receipts.add_subparsers(dest="receipts_command", required=True)
    parser_set = receipts_sub.add_parser("set", help="Record where a benchmark's binary lives")
    parser_set.add_argument("id", help="The wrapper's benchmark_id.")
    parser_set.add_argument("--binary", required=True, help="Path to the executable.")
    parser_set.add_argument(
        "--pre-run",
        action="append",
        dest="pre_run",
        help="Command to run before each launch (repeatable).",
    )
    parser_set.add_argument(
        "--launcher",
        default="",
        choices=["srun", "mpirun"],
        help="Override the preset's launcher for this benchmark (default: use the preset's).",
    )
    parser_set.add_argument(
        "--allow-missing",
        action="store_true",
        help="Record the path even if it does not exist here.",
    )
    parser_set.add_argument("--json", action="store_true", help="Print the saved receipt as JSON.")
    parser_set.set_defaults(func=handle_receipts_set)
