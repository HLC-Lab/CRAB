"""`crab parse` and `crab receipts set`.

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


def _differences(got: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    lines = []
    if len(got) != len(expected):
        lines.append(f"expected {len(expected)} rows, got {len(got)}")
    for i, (g, e) in enumerate(zip(got, expected, strict=False)):
        if g != e:
            lines.append(f"row {i}: expected {e}, got {g}")
    return lines


def handle_parse(args: argparse.Namespace) -> None:
    from crab.core.data.parse import ParseError, parse_output
    from crab.core.experiment.wrapper_paths import load_module, resolve_wrapper_path

    path = resolve_wrapper_path(args.wrapper)
    if not os.path.isfile(path):
        _fail(f"wrapper not found: {path}")

    app = load_module(path).app(0, True, args.args)
    for raw in args.set or []:
        key, value = _setting(raw)
        setattr(app, key, value)
    stderr = _read(args.stderr) if args.stderr else b""
    app.set_output(_read(args.output), stderr)
    app.run_dir = os.path.abspath(args.dir or os.path.dirname(args.output) or ".")

    try:
        parsed = parse_output(app)
    except ParseError as exc:
        _fail(str(exc))
    if parsed.legacy:
        print("crab: note: this wrapper returns the old list-per-metric shape", file=sys.stderr)

    if args.check:
        with open(args.check) as f:
            expected = json.load(f)
        diffs = _differences(parsed.rows, expected)
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
        "--launcher", default="", help="Launcher override, e.g. srun or mpirun."
    )
    parser_set.add_argument(
        "--allow-missing",
        action="store_true",
        help="Record the path even if it does not exist here.",
    )
    parser_set.add_argument("--json", action="store_true", help="Print the saved receipt as JSON.")
    parser_set.set_defaults(func=handle_receipts_set)
