#!/usr/bin/env python3
"""One-screen summary of the TSV that examples/nplb/topology.sh writes.

    topology_summary.py <topology.tsv> [--log-label TEXT]

The TSV has one header line, then one line per host and RDMA port (fields in FIELDS), and
"#WARN" / "#FAIL" records (record, host, text). The summary is at most 40 lines of 120 columns:
a header, a grid (rows = devices in natural order, columns = the first 4 hosts, cell = "<Gb/s>
<leaf>:<port>", "down", a trailing "b" for a bond member), then a "== VERDICT" block. Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_LINES = 40
MAX_COLS = 120
MAX_GRID_HOSTS = 4
FIELDS = (
    "host dev port state rate link_layer netdev operstate speed_mbps mac bond ipv4 gid pci numa "
    "lldp_sys lldp_port"
).split()
POD_RULE = "pods: nodes that share a switch heard over LLDP (named by their switches)"
NO_LLDP = "LLDP not visible on hosts: use the switch reader"
ARROW = "→"


@dataclass
class Port:
    host: str
    dev: str
    port: str
    state: str
    rate: str
    speed_mbps: str
    bond: str
    pci: str
    lldp_sys: str
    lldp_port: str

    @property
    def key(self) -> str:
        return self.dev if self.port in ("1", "-") else f"{self.dev}/{self.port}"

    @property
    def active(self) -> bool:
        return self.state.startswith("4")

    @property
    def down(self) -> bool:
        return self.state[:1].isdigit() and not self.active

    @property
    def leaf(self) -> str | None:
        return None if self.lldp_sys == "-" else leaf_label(self.lldp_sys)


# --- labels ---------------------------------------------------------------------------------


def natural_key(name: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def leaf_label(sysname: str) -> str:
    """`NV_Leaf3_CE9866_x` -> L3, `Spine2...` -> S2, anything else (a host): its short name, at
    most 8 characters."""
    if sysname == "-":
        return "-"
    leaf = re.search(r"leaf(\d+)", sysname, re.IGNORECASE)
    if leaf:
        return f"L{leaf.group(1)}"
    spine = re.search(r"spine(\d+)", sysname, re.IGNORECASE)
    if spine:
        return f"S{spine.group(1)}"
    return short(sysname)[:8]


def is_switch(label: str) -> bool:
    return re.fullmatch(r"[LS]\d+", label) is not None


def port_label(port: str) -> str:
    """`400GE1/1/5` -> 1/1/5, `400GE1/0/2:1` (breakout lane) -> 1/0/2:1, anything without a
    slashed number: its last 8 characters."""
    if port == "-":
        return "-"
    found = re.search(r"\d+(?:/\d+)+(?::\d+)?", port)
    return found.group(0) if found else port[-8:]


def pods_of(heard: dict[str, set[str]]) -> dict[str, str]:
    """Pod of each host: hosts that hear a common switch are one pod, named by all the switches
    its hosts hear ("L5/L6"); a host that hears no switch (only hosts, or nothing) gets "?"."""
    switches = {h: {s for s in labels if is_switch(s)} for h, labels in heard.items()}
    groups: list[tuple[set[str], set[str]]] = []  # (hosts, switches)
    for h, sw in switches.items():
        if not sw:
            continue
        touching = [g for g in groups if g[1] & sw]
        merged = ({h}, set(sw))
        for g in touching:
            merged[0].update(g[0])
            merged[1].update(g[1])
            groups.remove(g)
        groups.append(merged)
    pods = {h: "?" for h in heard}
    for members, sw in groups:
        name = "/".join(sorted(sw, key=natural_key))
        for h in members:
            pods[h] = name
    return pods


def short(host: str) -> str:
    return host.split(".")[0]


def gbps(port: Port) -> str:
    """Line speed in Gb/s: the netdev speed, else the number of the IB rate ("400 Gb/sec")."""
    try:
        mbps = int(port.speed_mbps)
    except ValueError:
        mbps = 0
    if mbps > 0:
        return f"{mbps / 1000:g}"
    rate = re.match(r"\s*([\d.]+)\s*Gb/sec", port.rate)
    return f"{float(rate.group(1)):g}" if rate else "?"


def cell(port: Port) -> str:
    if port.down:
        return "down"
    if not port.active:
        return "?"
    where = "-"
    if port.leaf is not None:
        where = f"{port.leaf}:{port_label(port.lldp_port)}"
    text = f"{gbps(port)} {where}"
    return text + " b" if port.bond != "-" else text


# --- parsing --------------------------------------------------------------------------------


def parse(text: str) -> tuple[list[str], dict[str, list[Port]], list[tuple[str, str, str]]]:
    """(hosts in order of first appearance, ports per host, [(WARN|FAIL, host, text)])."""
    hosts: list[str] = []
    ports: dict[str, list[Port]] = {}
    notes: list[tuple[str, str, str]] = []

    def see(host: str) -> None:
        if host not in ports:
            hosts.append(host)
            ports[host] = []

    for line in text.splitlines():
        if not line.strip() or line.startswith("host\t"):
            continue
        parts = line.split("\t")
        if parts[0] in ("#WARN", "#FAIL"):
            parts += ["-"] * (3 - len(parts))
            see(parts[1])
            notes.append((parts[0][1:], parts[1], parts[2]))
            continue
        parts += ["-"] * (len(FIELDS) - len(parts))
        f = dict(zip(FIELDS, parts, strict=False))
        see(f["host"])
        ports[f["host"]].append(
            Port(
                host=f["host"],
                dev=f["dev"],
                port=f["port"],
                state=f["state"],
                rate=f["rate"],
                speed_mbps=f["speed_mbps"],
                bond=f["bond"],
                pci=f["pci"],
                lldp_sys=f["lldp_sys"],
                lldp_port=f["lldp_port"],
            )
        )
    return hosts, ports, notes


# --- rendering ------------------------------------------------------------------------------


def fit(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= MAX_COLS else line[: MAX_COLS - 3] + "..."


def wrap(prefix: str, entries: list[str], sep: str = "; ") -> list[str]:
    """`prefix` + entries joined by `sep`, broken into lines of at most MAX_COLS columns."""
    lines: list[str] = []
    cur = prefix
    first = True
    for entry in entries:
        add = entry if first else sep + entry
        if not first and len(cur) + len(add.rstrip()) > MAX_COLS:
            lines.append(cur.rstrip())
            cur = "  " + entry
        else:
            cur += add
        first = False
    lines.append(cur.rstrip())
    return lines


def grid(
    hosts: list[str],
    ports: dict[str, list[Port]],
    unreachable: set[str],
    max_rows: int,
) -> list[str]:
    shown = hosts[:MAX_GRID_HOSTS]
    devs = sorted({p.key for h in shown for p in ports[h]}, key=natural_key)
    cells: dict[str, dict[str, str]] = {h: {p.key: cell(p) for p in ports[h]} for h in shown}
    heads = [f"n{i + 1}:{short(h)}" for i, h in enumerate(shown)]
    label_w = max([len("dev")] + [len(d) for d in devs])
    widths = []
    for i, h in enumerate(shown):
        col = [len(heads[i])] + [len(c) for c in cells[h].values()]
        if h in unreachable:
            col.append(len("unreachable"))
        widths.append(max(col))

    def row(label: str, values: list[str]) -> str:
        parts = [f"{label:<{label_w}}"] + [f"{v:<{w}}" for v, w in zip(values, widths, strict=True)]
        return fit("  ".join(parts))

    lines = [row("dev", heads)]
    for dev in devs[:max_rows]:
        values = ["unreachable" if h in unreachable else cells[h].get(dev, "n/a") for h in shown]
        lines.append(row(dev, values))
    if len(devs) > max_rows:
        lines.append(f"... {len(devs) - max_rows} more devices in the log")
    return lines


def card_patterns(host_ports: list[Port]) -> tuple[int, Counter[tuple[str, str]]]:
    """(cards, how often each (f0 leaf, f1 leaf) occurs among the cards heard on both functions)."""
    cards: dict[str, dict[str, str | None]] = {}
    for p in host_ports:
        card, dot, func = p.pci.rpartition(".")
        if p.pci == "-" or not dot or func not in ("0", "1"):
            continue
        cards.setdefault(card, {})[func] = p.leaf if p.active else None
    patterns: Counter[tuple[str, str]] = Counter()
    for funcs in cards.values():
        f0, f1 = funcs.get("0"), funcs.get("1")
        if f0 is not None and f1 is not None:
            patterns[(f0, f1)] += 1
    return len(cards), patterns


def speeds(host_ports: list[Port]) -> str:
    counts = Counter(gbps(p) for p in host_ports if p.active)
    return "+".join(
        f"{n}x{s}G" for s, n in sorted(counts.items(), key=lambda kv: natural_key(kv[0]))
    )


def verdicts(
    hosts: list[str],
    ports: dict[str, list[Port]],
    unreachable: set[str],
) -> list[str]:
    compact = len(hosts) > MAX_GRID_HOSTS
    leaves: dict[str, set[str]] = {
        h: {p.leaf for p in ports[h] if p.leaf is not None and p.active} for h in hosts
    }
    pods = pods_of(leaves)
    out = [POD_RULE]

    pod_entries = []
    for h in hosts:
        if h in unreachable:
            pod_entries.append(f"{short(h)}: unreachable")
        elif not leaves[h]:
            pod_entries.append(f"{short(h)}: pod ? (no leaf heard)")
        else:
            others = " ".join(sorted((s for s in leaves[h] if not is_switch(s)), key=natural_key))
            text = f"{short(h)}: pod {pods[h]}"
            if pods[h] == "?":
                text += " (no switch heard"
                text += f"; also {others})" if others else ")"
            elif others:
                text += f" (also {others})"
            pod_entries.append(text)
    out += wrap("hosts: ", pod_entries) if compact else pod_entries

    def relation(a: str, b: str) -> str:
        if pods[a] == "?" or pods[b] == "?":
            return "unknown"
        return "same-pod" if pods[a] == pods[b] else "cross-pod"

    if len(hosts) > 1:
        pairs = [f"{short(hosts[0])}-{short(h)} {relation(hosts[0], h)}" for h in hosts[1:]]
        out += wrap("pairs: ", pairs, ", ")
    if len(hosts) >= 3:
        rel = relation(hosts[0], hosts[2])
        crosses = {"cross-pod": "YES", "same-pod": "NO"}.get(rel, "UNKNOWN")
        out.append(f"victim pair (n1+n3) crosses the spine: {crosses}")
    else:
        out.append("victim pair (n1+n3) crosses the spine: UNKNOWN (needs 3 hosts)")

    port_entries = []
    for h in hosts:
        if h in unreachable:
            continue
        active = [p for p in ports[h] if p.active]
        text = f"{short(h)}: {len(active)} active"
        if speeds(ports[h]):
            text += f", {speeds(ports[h])}"
        downs = sum(1 for p in ports[h] if p.down)
        if downs:
            text += f", {downs} down"
        port_entries.append(text)
    out += wrap("ports: ", port_entries) if compact else port_entries

    card_entries = []
    for h in hosts:
        total, patterns = card_patterns(ports[h])
        if not total:
            continue
        if patterns:
            (f0, f1), n = patterns.most_common(1)[0]
            card_entries.append(f"{short(h)} f0{ARROW}{f0} f1{ARROW}{f1} on {n}/{total}")
        else:
            card_entries.append(f"{short(h)} f0/f1 both heard on 0/{total}")
    if card_entries:
        out += wrap("cards: ", card_entries)

    bond_entries = []
    for h in hosts:
        members: dict[str, list[str]] = {}
        for p in ports[h]:
            if p.bond != "-":
                members.setdefault(p.bond, []).append(p.key)
        for bond, devs in sorted(members.items()):
            bond_entries.append(f"{short(h)} {bond} ({' '.join(sorted(devs, key=natural_key))})")
    out += wrap("bonds: ", bond_entries) if bond_entries else ["bonds: none"]

    active_all = [p for h in hosts for p in ports[h] if p.active]
    heard = sum(1 for p in active_all if p.leaf is not None)
    out.append(f"LLDP heard on {heard}/{len(active_all)} active ports")
    if heard == 0:
        out.append(NO_LLDP)
    return out


def note_lines(notes: list[tuple[str, str, str]]) -> list[str]:
    return [f"{level} {short(host)}: {text}" for level, host, text in notes]


def when_of(path: str) -> str:
    found = re.search(r"(\d{8})-(\d{6})", os.path.basename(path))
    try:
        if found:
            return datetime.strptime("".join(found.groups()), "%Y%m%d%H%M%S").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError):
        return "unknown time"


def render(text: str, log: str = "", when: str | None = None) -> list[str]:
    """The summary lines for the TSV `text`; `log` is the path shown, `when` the time shown."""
    hosts, ports, notes = parse(text)
    failed = {h for level, h, _ in notes if level == "FAIL"}
    unreachable = {h for h in failed if not ports[h]}
    if when is None:
        when = when_of(log) if log else "unknown time"
    names = " ".join(short(h) for h in hosts)
    top = [fit(f"topology: {len(hosts)} hosts ({names}), {when}")]
    if log:
        shown = log if len(log) + 5 <= MAX_COLS else "..." + log[-(MAX_COLS - 8) :]
        top.append(f"log: {shown}")
    verdict = verdicts(hosts, ports, unreachable)
    notes_out = note_lines(notes)
    note = []
    if len(hosts) > MAX_GRID_HOSTS:
        note.append(
            f"note: {len(hosts)} hosts, the grid shows the first {MAX_GRID_HOSTS} (rest: log)"
        )
    n_dev = len({p.key for h in hosts[:MAX_GRID_HOSTS] for p in ports[h]})
    # every line but the grid rows and the WARN/FAIL lines: header, grid header, note, verdicts
    room = max(MAX_LINES - (len(top) + 1 + len(note) + 1 + len(verdict)), 0)
    keep = len(notes_out) if n_dev + len(notes_out) <= room else min(len(notes_out), 3)
    overflow = 1 if keep < len(notes_out) else 0
    rows_room = max(room - keep - overflow, 0)
    rows = n_dev if n_dev <= rows_room else max(rows_room - 1, 0)
    lines = [*top, *grid(hosts, ports, unreachable, rows), *note, "== VERDICT", *verdict]
    lines += notes_out[:keep]
    if overflow:
        lines.append(f"... {len(notes_out) - keep} more WARN/FAIL lines in the log")
    lines = [fit(ln) for ln in lines]
    if len(lines) > MAX_LINES:
        lines = lines[: MAX_LINES - 1] + [f"... {len(lines) - MAX_LINES + 1} more lines in the log"]
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="One-screen summary of a topology TSV.")
    parser.add_argument("tsv")
    parser.add_argument("--log-label", default=None, help="path shown in the summary")
    args = parser.parse_args(argv)
    try:
        text = Path(args.tsv).read_text()
    except OSError as exc:
        print(f"topology_summary: cannot read {args.tsv}: {exc}", file=sys.stderr)
        return 2
    label = args.log_label if args.log_label is not None else args.tsv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(render(text, label, when_of(args.tsv))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
