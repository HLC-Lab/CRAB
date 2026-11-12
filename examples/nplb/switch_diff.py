#!/usr/bin/env python3
"""`nplb net diff`: what is different in the configuration of the leaves that fail?

    switch_diff.py DIR [--odd Leaf3,Leaf4]

DIR holds one `<name>.cfg` per switch (`nplb net dump` writes them). The ODD leaves (default Leaf3,Leaf4)
are compared with the other leaves (names that start with Leaf): config lines are normalised (every digit
becomes `N`, so port numbers and addresses do not count), then the report lists

    missing  lines every other leaf has and no odd leaf has
    extra    lines every odd leaf has and no other leaf has

Stdout is the report (the verb pages and saves it as net-diff), first line `VERDICT: ...`. At
most 12 lines are shown per list, each cut at 96 columns; the full lists are written to DIR/diff.txt.
Exit 0, or 2 for a missing folder or too few configs. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAX_SHOWN = 12
WIDTH = 96
SKIP = re.compile(
    r"^\s*(#|return|description|sysname|ssh|local-user|snmp|ntp|clock|!)", re.IGNORECASE
)


def normalise(line: str) -> str:
    return re.sub(r"\d+", "N", " ".join(line.split()))


def read_config(path: Path) -> set[str]:
    lines: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or SKIP.match(raw):
            continue
        lines.add(normalise(raw))
    return lines


def compare(configs: dict[str, set[str]], odd: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(odd names found, missing, extra) as sorted lists of normalised lines."""
    found_odd = [n for n in odd if n in configs]
    others = [n for n in configs if n.startswith("Leaf") and n not in odd]
    if not found_odd or not others:
        return found_odd, [], []
    other_all = set.intersection(*(configs[n] for n in others))
    other_any = set.union(*(configs[n] for n in others))
    odd_all = set.intersection(*(configs[n] for n in found_odd))
    odd_any = set.union(*(configs[n] for n in found_odd))
    return found_odd, sorted(other_all - odd_any), sorted(odd_all - other_any)


def cut(line: str) -> str:
    return line if len(line) <= WIDTH else line[: WIDTH - 1] + "~"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="switch_diff.py")
    parser.add_argument("dir")
    parser.add_argument("--odd", default="Leaf3,Leaf4")
    args = parser.parse_args(argv)
    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"{folder} is not a folder: run nplb net dump first", file=sys.stderr)
        return 2
    configs = {p.stem: read_config(p) for p in sorted(folder.glob("*.cfg"))}
    odd = [x for x in args.odd.split(",") if x]
    found_odd, missing, extra = compare(configs, odd)
    others = [n for n in configs if n.startswith("Leaf") and n not in odd]
    if not found_odd or not others:
        print(
            f"need configs of {odd} and of other leaves in {folder}, found {sorted(configs)}",
            file=sys.stderr,
        )
        return 2
    body = (
        "missing in odd leaves:\n"
        + "\n".join(missing)
        + "\n\nextra in odd leaves:\n"
        + "\n".join(extra)
        + "\n"
    )
    (folder / "diff.txt").write_text(body, encoding="utf-8")
    print(
        f"VERDICT: {'DIFF' if missing or extra else 'SAME'} {'/'.join(found_odd)} vs {'/'.join(others)}: "
        f"{len(missing)} lines missing, {len(extra)} extra"
    )
    for title, lines in (
        ("missing in the odd leaves", missing),
        ("extra in the odd leaves", extra),
    ):
        print(f"\n{title} ({len(lines)}):")
        for line in lines[:MAX_SHOWN]:
            print("  " + cut(line))
        if len(lines) > MAX_SHOWN:
            print(f"  ... {len(lines) - MAX_SHOWN} more in {folder / 'diff.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
