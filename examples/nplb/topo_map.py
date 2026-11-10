#!/usr/bin/env python3
"""The node-to-leaf map of the NPLB lab and a preview of the placement layouts.

    topo_map.py build --tsv FILE [--hosts H,...] [--out PATH] [--notes TEXT] [--probe-rc N]

FILE is the TSV that topology.sh writes (one header line, one line per host and RDMA port in the
FIELDS of topology_summary.py, "#WARN" / "#FAIL" records; extra columns are ignored). A node's
leaf is the SET of switches heard over LLDP on its ACTIVE ports (a node can hang on two leaves of
a pod); a port that is down, or heard nothing, adds nothing. Per host, `NODE_LEAF` ("host=leaf,
host=leaf", a value may name several leaves joined by "+") and then the file $WORK/node_leaf.txt
("host leaf" per line, "#" starts a comment) replace what LLDP gave; the env var wins over the file.

The hosts are taken in the order Slurm hands them to a job (`scontrol show hostnames`: sorted by
name, numbers in natural order), which no layout can change. The layouts follow
NodeAllocator.allocate_partitioned (src/crab/core/allocation/allocator.py) for two partitions of
share 50 (victim, aggressor), one app each: of 4 nodes, `split-i` (mode interleaved) puts the
victim on slots 0 and 2 and the aggressor on 1 and 3; `split-l` (mode linear) puts the victim on
slots 0 and 1 and the aggressor on 2 and 3; `shared` gives both partitions all the nodes. For each
layout the map says how the two nodes of each side relate (same = identical leaf sets, partial =
some leaf in common, cross = none, unknown = a leaf is not known) and which leaves host both sides.

It writes the map (JSON, through a temp file and a rename) to --out, default $WORK/node_map.json,
and prints the plain report on stdout; topo_tools.sh pages it. WORK is $WORK, else $NPLB_WORK, else
<checkout>/local/nplb-work. $NPLB_NOW (YYYY-MM-DDTHH:MM:SS) replaces the clock, for tests.
Exit 0 when the map was written, 1 when --probe-rc is not 0 and no leaf is known, 2 on bad input.
Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from topology_summary import FIELDS, leaf_label, natural_key

CHECKOUT = Path(__file__).resolve().parents[2]
LAYOUTS = (("split-i", "interleaved"), ("split-l", "linear"), ("shared", "shared"))
MAX_LEAVES_SHOWN = 36  # characters of the leaves column


class Fail(Exception):
    """A user-facing error: the message goes to stderr and the exit code is 2."""


@dataclass
class HostPorts:
    total: int = 0
    active: int = 0
    heard: set[str] = field(default_factory=set)  # leaf labels heard on active ports


# --- parsing --------------------------------------------------------------------------------


def is_active(state: str) -> bool:
    """sysfs prints "4: ACTIVE"; the first character is the numeric state."""
    return state.startswith("4") or "ACTIVE" in state.upper()


def parse_tsv(text: str) -> tuple[list[str], dict[str, HostPorts], set[str]]:
    """(hosts in order of appearance, ports per host, hosts with a #FAIL record)."""
    hosts: list[str] = []
    ports: dict[str, HostPorts] = {}
    failed: set[str] = set()

    def see(host: str) -> HostPorts:
        if host not in ports:
            hosts.append(host)
            ports[host] = HostPorts()
        return ports[host]

    for line in text.splitlines():
        if not line.strip() or line.startswith("host\t"):
            continue
        parts = line.split("\t")
        if parts[0] in ("#WARN", "#FAIL"):
            if len(parts) > 1 and parts[1].strip():
                see(parts[1].strip())
                if parts[0] == "#FAIL":
                    failed.add(parts[1].strip())
            continue
        parts += ["-"] * (len(FIELDS) - len(parts))
        row = dict(zip(FIELDS, parts, strict=False))
        entry = see(row["host"].strip())
        entry.total += 1
        if not is_active(row["state"]):
            continue
        entry.active += 1
        sysname = row["lldp_sys"].strip()
        if sysname and sysname != "-":
            entry.heard.add(leaf_label(sysname))
    return hosts, ports, failed


def parse_leaf_value(value: str) -> list[str]:
    return sorted({leaf.strip() for leaf in value.split("+") if leaf.strip()}, key=natural_key)


def parse_override_env(text: str) -> dict[str, list[str]]:
    """`host=leaf,host=leaf` -> {host: leaves}. Raises Fail on an entry that is not host=leaf."""
    found: dict[str, list[str]] = {}
    for entry in text.split(","):
        if not entry.strip():
            continue
        host, sep, value = entry.partition("=")
        leaves = parse_leaf_value(value)
        if not sep or not host.strip() or not leaves:
            raise Fail(f"NODE_LEAF: malformed entry {entry.strip()!r} (want host=leaf)")
        found[host.strip()] = leaves
    return found


def parse_override_file(text: str, name: str) -> dict[str, list[str]]:
    """`host leaf` lines, "#" comments -> {host: leaves}. Raises Fail on a line of another shape."""
    found: dict[str, list[str]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        leaves = parse_leaf_value(words[1]) if len(words) == 2 else []
        if not leaves:
            raise Fail(f"{name} line {number}: malformed entry {line!r} (want: host leaf)")
        found[words[0]] = leaves
    return found


# --- the map --------------------------------------------------------------------------------


def sort_names(names: list[str] | set[str]) -> list[str]:
    return sorted(names, key=natural_key)


def partition_slots(count: int, mode: str) -> tuple[list[int], list[int]]:
    """Node-list slots of (victim, aggressor) for COUNT nodes, as allocate_partitioned assigns
    them for two partitions of share 50: the largest-remainder counts give an odd node to the
    victim, `interleaved` (stride 1) deals the slots out alternately, `linear` gives the first
    block to the victim."""
    sizes = [count - count // 2, count // 2]
    if mode == "shared":
        everything = list(range(count))
        return everything, list(everything)
    if mode == "linear":
        return list(range(sizes[0])), list(range(sizes[0], count))
    parts: list[list[int]] = [[], []]
    slot = 0
    while slot < count:
        advanced = False
        for index in (0, 1):
            if slot < count and len(parts[index]) < sizes[index]:
                parts[index].append(slot)
                slot += 1
                advanced = True
        if not advanced:
            break
    return parts[0], parts[1]


def classify(sets: list[set[str] | None]) -> str:
    """same: identical leaf sets (or fewer than two nodes), cross: no leaf in common between any
    two, partial: anything between, unknown: some node's leaf is not known."""
    if any(s is None for s in sets):
        return "unknown"
    known = [s for s in sets if s is not None]
    if len(known) < 2 or all(s == known[0] for s in known):
        return "same"
    disjoint = all(
        not (known[i] & known[j]) for i in range(len(known)) for j in range(i + 1, len(known))
    )
    return "cross" if disjoint else "partial"


def used_leaves(hosts: list[str], leaf: dict[str, list[str] | None]) -> set[str]:
    return {name for host in hosts for name in leaf[host] or []}


def build_layouts(
    hosts: list[str], leaf: dict[str, list[str] | None]
) -> dict[str, dict[str, object]]:
    layouts: dict[str, dict[str, object]] = {}
    for name, mode in LAYOUTS:
        victim_slots, aggressor_slots = partition_slots(len(hosts), mode)
        victim = [hosts[i] for i in victim_slots]
        aggressor = [hosts[i] for i in aggressor_slots]

        def sets_of(group: list[str]) -> list[set[str] | None]:
            return [None if leaf[h] is None else set(leaf[h] or []) for h in group]

        layouts[name] = {
            "victim": victim,
            "aggressor": aggressor,
            "victim_pair": classify(sets_of(victim)),
            "aggressor_pair": classify(sets_of(aggressor)),
            "shared_leaves": sort_names(used_leaves(victim, leaf) & used_leaves(aggressor, leaf)),
        }
    return layouts


def describe_source(lldp_hosts: int, manual_hosts: int, hosts: int) -> str:
    """`manual` when every leaf came from an override, else the sources used + `slurm-order`."""
    if manual_hosts == hosts and hosts > 0:
        return "manual"
    parts = []
    if lldp_hosts:
        parts.append("lldp")
    if manual_hosts:
        parts.append("manual")
    parts.append("slurm-order")
    return "+".join(parts)


def make_verdict(
    hosts: list[str],
    leaf: dict[str, list[str] | None],
    manual: set[str],
    origin: str,
    failed: set[str],
    probe_rc: int,
) -> str:
    unknown = [h for h in hosts if leaf[h] is None]
    status = "OK"
    if len(manual) == len(hosts):
        text = f"leaves from manual override ({origin})"
    elif len(unknown) == len(hosts):
        status = "WARN"
        text = "leaf unknown for all nodes: set NODE_LEAF=host=leaf,... or $WORK/node_leaf.txt"
    elif unknown:
        status = "WARN"
        why = "probe failed" if all(h in failed for h in unknown) else "no LLDP heard"
        text = f"leaf unknown for {', '.join(unknown)} ({why})"
    else:
        text = f"leaves known for {len(hosts)} of {len(hosts)} nodes"
    if probe_rc != 0:
        status = "WARN"
        # first, so the pager's 100-column cut never hides it
        text = (
            f"probe exit {probe_rc}" + (" (a node failed)" if probe_rc == 1 else "") + f": {text}"
        )
    return f"VERDICT: {status} {text}"


# --- the report -----------------------------------------------------------------------------


def leaves_cell(names: list[str] | None) -> str:
    if names is None:
        return "?"
    text = "+".join(names)
    return text if len(text) <= MAX_LEAVES_SHOWN else text[: MAX_LEAVES_SHOWN - 3] + "..."


def host_table(
    hosts: list[str],
    leaf: dict[str, list[str] | None],
    ports: dict[str, HostPorts],
    manual: set[str],
) -> list[str]:
    cells = []
    for host in hosts:
        entry = ports.get(host, HostPorts())
        if host in manual:
            source = "manual"
        elif leaf[host] is not None:
            source = "lldp"
        else:
            source = "none"
        cells.append((host, leaves_cell(leaf[host]), f"{entry.active}/{entry.total}", source))
    widths = [
        max(len(c[i]) for c in cells + [("host", "leaves", "ports up", "source")]) for i in range(3)
    ]
    lines = [f"{'host':<{widths[0]}}  {'leaves':<{widths[1]}}  {'ports up':<{widths[2]}}  source"]
    for host, leaves, up, source in cells:
        lines.append(f"{host:<{widths[0]}}  {leaves:<{widths[1]}}  {up:<{widths[2]}}  {source}")
    return lines


def layout_table(layouts: dict[str, dict[str, object]]) -> list[str]:
    rows = []
    for name, info in layouts.items():
        victim = " ".join(info["victim"])  # type: ignore[arg-type]
        aggressor = " ".join(info["aggressor"])  # type: ignore[arg-type]
        shared = " ".join(info["shared_leaves"]) or "-"  # type: ignore[arg-type]
        rows.append((name, "victim", victim, str(info["victim_pair"]), shared))
        rows.append(("", "aggressor", aggressor, str(info["aggressor_pair"]), ""))
    head = ("layout", "role", "nodes", "pair", "shared leaves")
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(4)]
    out = []
    for row in [head, *rows]:
        cells = [f"{row[i]:<{widths[i]}}" for i in range(4)] + [row[4]]
        out.append("  ".join(cells).rstrip())
    return out


def render(
    verdict: str,
    notes: str,
    hosts: list[str],
    leaf: dict[str, list[str] | None],
    ports: dict[str, HostPorts],
    manual: set[str],
    layouts: dict[str, dict[str, object]],
    source: str,
    saved: Path,
) -> list[str]:
    lines = [verdict]
    if manual:
        lines.append(f"overridden by NODE_LEAF or node_leaf.txt: {', '.join(sort_names(manual))}")
    if notes:
        lines.append(notes)
    if source != "manual" and all(leaf[h] is None for h in hosts):
        lines.append("no LLDP was visible: the pair classes are unknown until a leaf is given")
    lines += [
        f"map source: {source}",
        "Slurm hands the job its nodes in sorted-name order and no layout can reorder them.",
        "",
    ]
    lines += host_table(hosts, leaf, ports, manual)
    lines.append("")
    lines += layout_table(layouts)
    lines += [
        "",
        "split-i: victim on nodes 0 and 2, aggressor on 1 and 3 (CRAB interleaved).",
        "split-l: victim on nodes 0 and 1, aggressor on 2 and 3 (CRAB linear).",
        "pair: same = identical leaf sets, partial = some leaf in common, cross = none in common,",
        "unknown = a leaf is not known. shared leaves = leaves hosting both victim and aggressor.",
        f"map saved: {saved}",
    ]
    return lines


# --- main -----------------------------------------------------------------------------------


def work_dir() -> Path:
    explicit = os.environ.get("WORK") or os.environ.get("NPLB_WORK")
    return Path(explicit) if explicit else CHECKOUT / "local" / "nplb-work"


def now_iso() -> str:
    fixed = os.environ.get("NPLB_NOW")
    if not fixed:
        return datetime.now().isoformat(timespec="seconds")
    try:
        return datetime.strptime(fixed, "%Y-%m-%dT%H:%M:%S").isoformat(timespec="seconds")
    except ValueError:
        raise Fail(f"NPLB_NOW={fixed!r} is not YYYY-MM-DDTHH:MM:SS") from None


def read_overrides(work: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(host -> leaves, host -> where it came from: NODE_LEAF or node_leaf.txt); env wins."""
    merged: dict[str, list[str]] = {}
    origin: dict[str, str] = {}
    path = work / "node_leaf.txt"
    if path.is_file():
        try:
            from_file = parse_override_file(path.read_text(), str(path))
        except OSError as err:
            raise Fail(f"cannot read {path}: {err}") from None
        for host, leaves in from_file.items():
            merged[host], origin[host] = leaves, "node_leaf.txt"
    for host, leaves in parse_override_env(os.environ.get("NODE_LEAF", "")).items():
        merged[host], origin[host] = leaves, "NODE_LEAF"
    return merged, origin


def write_json_atomic(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def build(args: argparse.Namespace) -> int:
    try:
        text = Path(args.tsv).read_text()
    except OSError as err:
        raise Fail(f"cannot read {args.tsv}: {err.strerror}") from None
    seen, ports, failed = parse_tsv(text)
    wanted = [h.strip() for h in args.hosts.split(",") if h.strip()] if args.hosts else seen
    hosts = sort_names(set(wanted))
    if not hosts:
        raise Fail(f"no hosts in {args.tsv}")
    work = work_dir()
    overrides, origin = read_overrides(work)

    leaf: dict[str, list[str] | None] = {}
    manual = {h for h in hosts if h in overrides}
    lldp_hosts = 0
    for host in hosts:
        if host in manual:
            leaf[host] = overrides[host]
            continue
        heard = ports[host].heard if host in ports else set()
        leaf[host] = sort_names(heard) if heard else None
        lldp_hosts += 1 if heard else 0
    unknown = sum(1 for h in hosts if leaf[h] is None)
    source = describe_source(lldp_hosts, len(manual), len(hosts))
    layouts = build_layouts(hosts, leaf)
    out = Path(args.out) if args.out else work / "node_map.json"
    write_json_atomic(
        out,
        {
            "when": now_iso(),
            "source": source,
            "hosts": hosts,
            "leaf": leaf,
            "layouts": layouts,
        },
    )
    origins = {origin[h] for h in manual}
    verdict = make_verdict(
        hosts,
        leaf,
        manual,
        "NODE_LEAF" if "NODE_LEAF" in origins else "node_leaf.txt",
        failed,
        args.probe_rc,
    )
    print("\n".join(render(verdict, args.notes, hosts, leaf, ports, manual, layouts, source, out)))
    return 1 if args.probe_rc != 0 and unknown == len(hosts) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NPLB node-to-leaf map and layout preview")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build", help="build the node map and print the report")
    one.add_argument("--tsv", required=True, help="the TSV topology.sh wrote")
    one.add_argument("--hosts", default="", help="comma-separated hosts (default: the TSV's)")
    one.add_argument("--out", default="", help="the map file (default $WORK/node_map.json)")
    one.add_argument("--notes", default="", help="one line of text for the report")
    one.add_argument("--probe-rc", type=int, default=0, help="exit status of the probe")
    args = parser.parse_args(argv)
    try:
        return build(args)
    except Fail as err:
        print(f"topo_map: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
