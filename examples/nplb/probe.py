#!/usr/bin/env python3
"""Cluster probe for the NPLB campaign: RDMA NICs and where they sit.

Run once per node (stdlib only, Python 3.10+), then merge the lines:

    srun -N4 --ntasks-per-node=1 python3 examples/nplb/probe.py --json > probe.jsonl
    python3 examples/nplb/probe.py --merge probe.jsonl --out profile.json

Modes:
  probe.py [--sysfs ROOT] [--no-gpu]        readable table for this node
  probe.py --json [...]                     one JSON line for this node
  probe.py --merge FILE... [--out FILE] [--per-card]
                                            per-node lines (or "-" for stdin) in, table and
                                            warnings on stderr, cluster profile JSON out

The profile's `nics` and `cpus` can be pasted into a CRAB app config. CRAB sorts an app's `cpus`,
and under srun local rank i gets the i-th core of the sorted list, while `nics[i]` is rank i's
NIC: so each NIC is paired with a core next to it, and the pairs are sorted by core. With
`--per-card` a multi-function card (several devices on one PCI slot) contributes one NIC, not one
per device.

Everything is read from sysfs (default /sys); a missing file is recorded as null.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

GPU_TIMEOUT_S = 20
MEM_SPREAD_WARN = 0.05  # nodes whose memory differs by more than this get a warning


# --- sysfs helpers ----------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    """Stripped text of a sysfs file, or None when it is missing or unreadable."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _list_dir(path: Path) -> list[str] | None:
    try:
        return sorted(entry.name for entry in path.iterdir())
    except OSError:
        return None


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def parse_cpulist(text: str | None) -> list[int]:
    """Cores of a kernel cpulist such as "0-15,64-79". Unparsable input gives an empty list."""
    cores: set[int] = set()
    if not text:
        return []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        lo, sep, hi = item.partition("-")
        try:
            first = int(lo)
            last = int(hi) if sep else first
        except ValueError:
            return []
        if last < first:
            return []
        cores.update(range(first, last + 1))
    return sorted(cores)


def format_cpus(cores: Iterable[int]) -> str:
    """Comma-separated core numbers, as CRAB's `cpus` app key takes them."""
    return ",".join(str(c) for c in cores)


def _rate_mbps(rate: str | None) -> int | None:
    """Mb/s of a port rate like "400 Gb/sec (4X NDR)" (the number may carry a fraction)."""
    if not rate:
        return None
    match = re.match(r"\s*([\d.]+)\s*([GMT]?)b/sec", rate)
    if not match:
        return None
    unit = {"": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6}[match.group(2)]
    try:
        return round(float(match.group(1)) * unit)
    except ValueError:
        return None


# --- collecting one node ----------------------------------------------------------------------


def _mem_gib(proc: Path) -> float | None:
    """MemTotal of `<proc>/meminfo` in GiB (one decimal), None when missing or unparsable."""
    text = _read(proc / "meminfo")
    if text is None:
        return None
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.MULTILINE)
    if not match:
        return None
    return round(int(match.group(1)) / (1024 * 1024), 1)


def _gpu_topo() -> str | None:
    """Raw `nvidia-smi topo -m` text, None without nvidia-smi, failure text on errors."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, "topo", "-m"],
            capture_output=True,
            text=True,
            timeout=GPU_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"nvidia-smi failed: {exc}"
    if done.returncode != 0:
        return f"nvidia-smi exited {done.returncode}: {(done.stderr or done.stdout).strip()}"
    return done.stdout


def _env_int(environ: Mapping[str, str], key: str) -> int | None:
    try:
        return int(environ[key])
    except (KeyError, ValueError):
        return None


def _pci_address(base: Path) -> str | None:
    """PCI address (e.g. 0000:19:00.0) of a device: where its `device` symlink points, else None."""
    link = base / "device"
    if not link.is_symlink():
        return None
    return os.path.basename(os.path.realpath(link)) or None


def collect_node(
    sysfs: Path,
    with_gpu: bool = True,
    hostname: str | None = None,
    environ: Mapping[str, str] | None = None,
    proc: Path = Path("/proc"),
) -> dict[str, Any]:
    """Describe this node's RDMA devices and memory as a JSON-ready dict (missing data is None)."""
    env = os.environ if environ is None else environ
    devices: dict[str, Any] = {}
    ib_root = sysfs / "class" / "infiniband"
    for dev in sorted(_list_dir(ib_root) or [], key=_natural_key):
        base = ib_root / dev
        ports: dict[str, Any] = {}
        port_root = base / "ports"
        for port in sorted(_list_dir(port_root) or [], key=_natural_key):
            ports[port] = {
                "state": _read(port_root / port / "state"),
                "rate": _read(port_root / port / "rate"),
                "link_layer": _read(port_root / port / "link_layer"),
            }
        netdevs = _list_dir(base / "device" / "net") or []
        devices[dev] = {
            "pci": _pci_address(base),
            "numa": _read_int(base / "device" / "numa_node"),
            "cpulist": _read(base / "device" / "local_cpulist"),
            "netdevs": netdevs,
            "netdev_speeds": {
                nd: _read_int(sysfs / "class" / "net" / nd / "speed") for nd in netdevs
            },
            "ports": ports,
        }
    numa_cpulists: dict[str, str | None] = {}
    node_root = sysfs / "devices" / "system" / "node"
    for entry in sorted(_list_dir(node_root) or [], key=_natural_key):
        if re.fullmatch(r"node\d+", entry):
            numa_cpulists[entry[4:]] = _read(node_root / entry / "cpulist")
    return {
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "procid": _env_int(env, "SLURM_PROCID"),
        "nodeid": _env_int(env, "SLURM_NODEID"),
        "devices": devices,
        "numa_cpulists": numa_cpulists,
        "mem_gib": _mem_gib(proc),
        "gpu_topo": _gpu_topo() if with_gpu else None,
    }


# --- reading merged input ---------------------------------------------------------------------


def parse_node_lines(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """Node dicts from per-node JSON lines, in Slurm (procid) order, and the junk-line count.

    Blank lines are skipped silently; any other line that is not a node object (srun noise)
    is counted. Without a procid on every node the input order is kept.
    """
    nodes: list[dict[str, Any]] = []
    junk = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            junk += 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("devices"), dict):
            nodes.append(obj)
        else:
            junk += 1
    if nodes and all(isinstance(n.get("procid"), int) for n in nodes):
        nodes.sort(key=lambda n: n["procid"])
    return nodes, junk


# --- profile ----------------------------------------------------------------------------------


def _active_ports(node: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """`<dev>:<port>` -> port data (plus the device) for every ACTIVE port of the node."""
    found: dict[str, dict[str, Any]] = {}
    for dev, info in node.get("devices", {}).items():
        for port, data in (info.get("ports") or {}).items():
            if "ACTIVE" in (data.get("state") or ""):
                found[f"{dev}:{port}"] = {"dev": dev, "port": port, **data}
    return found


def _card_of(pci: str) -> str:
    """PCI slot of an address with the function stripped: 0000:19:00.1 -> 0000:19:00."""
    return pci.rpartition(".")[0] or pci


def _function_key(pci: str, dev: str) -> list[Any]:
    """Order of a card's devices: by PCI function, then by name."""
    return [_natural_key(pci.rpartition(".")[2]), _natural_key(dev)]


def _one_port_per_card(node: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """One ACTIVE port per physical card (devices sharing a PCI slot), and the warnings.

    A card keeps the lowest active port of its first device (by PCI function) that has one; a
    card with no active port is left out with a warning. Raises ValueError when a device has no
    PCI address (probe output from before the address was recorded): the grouping would be a guess.
    """
    devices = node.get("devices", {})
    unknown = sorted((d for d, info in devices.items() if not info.get("pci")), key=_natural_key)
    if unknown:
        raise ValueError(
            f"--per-card needs PCI addresses but {node.get('hostname')} has none for "
            f"{', '.join(unknown)}: run the current probe.py on the nodes again"
        )
    active = _active_ports(node)
    cards: dict[str, list[str]] = {}
    for dev in sorted(devices, key=lambda d: _function_key(devices[d]["pci"], d)):
        cards.setdefault(_card_of(devices[dev]["pci"]), []).append(dev)
    chosen: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for card in sorted(cards, key=_natural_key):
        names = [
            sorted((n for n, p in active.items() if p["dev"] == dev), key=_natural_key)
            for dev in cards[card]
        ]
        first = next((ports for ports in names if ports), None)
        if first is None:
            warnings.append(f"card {card} has no active port; left out")
            continue
        chosen[first[0]] = active[first[0]]
    return chosen, warnings


def _selected_ports(
    node: Mapping[str, Any], per_card: bool
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The ports that become NICs (every active port, or one per card) and per-card warnings."""
    if per_card:
        return _one_port_per_card(node)
    return _active_ports(node), []


def _netdev_of(info: Mapping[str, Any], port: str) -> str | None:
    """The netdev of a port: the only one, or the i-th when there are as many as ports."""
    netdevs = info.get("netdevs") or []
    ports = sorted(info.get("ports") or {}, key=_natural_key)
    if len(netdevs) == 1:
        return str(netdevs[0])
    if netdevs and len(netdevs) == len(ports):
        return str(sorted(netdevs, key=_natural_key)[ports.index(port)])
    return None


def _pick_core(
    taken: set[int], local: list[int], numa_cores: list[int], everything: list[int]
) -> tuple[int, str]:
    """Lowest free core of the first level that has one: local, NUMA node, anywhere, any int."""
    for source, cores in (("local", local), ("numa", numa_cores), ("any", everything)):
        for core in cores:
            if core not in taken:
                return core, source
    core = 0
    while core in taken:
        core += 1
    return core, "any"


def _port_warnings(node: Mapping[str, Any], active: Mapping[str, Any]) -> list[str]:
    """Warnings for ports that are not active and for devices with several active ports."""
    warnings: list[str] = []
    for dev, info in node.get("devices", {}).items():
        count = 0
        for port, data in (info.get("ports") or {}).items():
            if f"{dev}:{port}" not in active:
                state = data.get("state") or "unknown state"
                warnings.append(f"{dev}:{port} is not active ({state}); left out")
            else:
                count += 1
        if count > 1:
            warnings.append(
                f"{dev} has {count} active ports (possible dual-port NIC exposed as one device): "
                "report before continuing"
            )
    return warnings


def _layout(
    node: Mapping[str, Any], per_card: bool = False
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Per-NIC layout of a node (numa, core, rate, netdev, speed) and the warnings it raises."""
    devices = node.get("devices", {})
    numa_lists = {
        int(k): parse_cpulist(v)
        for k, v in (node.get("numa_cpulists") or {}).items()
        if k.isdigit()
    }
    everything = sorted({c for cores in numa_lists.values() for c in cores})
    active, warnings = _selected_ports(node, per_card)
    if not per_card:  # with per-card, a card's other ports are expected to be left out
        warnings.extend(_port_warnings(node, active))
    taken: set[int] = set()
    layout: dict[str, dict[str, Any]] = {}
    for name in sorted(active, key=_natural_key):
        port = active[name]
        info = devices[port["dev"]]
        numa = info.get("numa")
        if numa is None or numa < 0:
            numa = None
            warnings.append(f"{name}: NUMA node unknown")
        local = parse_cpulist(info.get("cpulist"))
        numa_cores = numa_lists.get(numa, []) if numa is not None else []
        core, source = _pick_core(taken, local, numa_cores, everything)
        if source != "local":
            where = {"numa": f"NUMA node {numa} cores", "any": "any core"}[source]
            warnings.append(f"{name}: no usable local_cpulist; took core {core} from {where}")
        taken.add(core)
        netdev = _netdev_of(info, port["port"])
        speed = (info.get("netdev_speeds") or {}).get(netdev) if netdev else None
        if speed is not None and speed < 0:
            speed = None
        rate_mbps = _rate_mbps(port.get("rate"))
        if speed is not None and rate_mbps is not None and speed != rate_mbps:
            warnings.append(
                f"{name}: netdev {netdev} speed {speed} Mb/s differs from port rate {port['rate']}"
            )
        layout[name] = {
            "numa": numa,
            "core": core,
            "rate": port.get("rate"),
            "netdev": netdev,
            "speed_mbps": speed,
        }
        if info.get("pci"):  # absent (not null) for probe lines from before it was recorded
            layout[name]["pci"] = info["pci"]
    return layout, warnings


def _differences(
    first: Mapping[str, Any], other: Mapping[str, Any], host: str, ref: str
) -> list[str]:
    """Warnings for how `other` (a node's per-NIC layout) differs from the first node's."""
    out: list[str] = []
    missing = sorted(set(first) - set(other), key=_natural_key)
    extra = sorted(set(other) - set(first), key=_natural_key)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("extra " + ", ".join(extra))
        out.append(f"{host}: active NICs differ from {ref} ({'; '.join(parts)})")
    for name in sorted(set(first) & set(other), key=_natural_key):
        if first[name]["numa"] != other[name]["numa"]:
            out.append(
                f"{host}: {name} is on NUMA node {other[name]['numa']}, {ref} has "
                f"{first[name]['numa']}"
            )
        if first[name]["rate"] != other[name]["rate"]:
            out.append(
                f"{host}: {name} rate is {other[name]['rate']}, {ref} has {first[name]['rate']}"
            )
    return out


def build_profile(
    nodes: list[dict[str, Any]], junk_lines: int = 0, per_card: bool = False
) -> dict[str, Any]:
    """Cluster profile for the nodes (already in Slurm order): the first node sets the layout.

    With `per_card` every physical card (PCI slot) gives one NIC; raises ValueError when a node
    has devices without a recorded PCI address.
    """
    warnings: list[str] = []
    if junk_lines:
        plural = "" if junk_lines == 1 else "s"
        warnings.append(f"ignored {junk_lines} non-JSON line{plural} (srun noise?)")
    if not nodes:
        warnings.append("no node data")
        return {
            "nodes": [],
            "ppn": 0,
            "nics": [],
            "cpus": "",
            "link_layer": None,
            "per_card": per_card,
            "per_nic": {},
            "mem_gib": None,
            "warnings": warnings,
        }
    layout, first_warnings = _layout(nodes[0], per_card)
    warnings.extend(first_warnings)
    layers = {p.get("link_layer") for p in _selected_ports(nodes[0], per_card)[0].values()}
    ref = str(nodes[0].get("hostname"))
    for node in nodes[1:]:
        other, other_warnings = _layout(node, per_card)
        host = str(node.get("hostname"))
        warnings.extend(f"{host}: {w}" for w in other_warnings)
        warnings.extend(_differences(layout, other, host, ref))
        layers.update(p.get("link_layer") for p in _selected_ports(node, per_card)[0].values())
    layers.discard(None)
    if not layout:
        warnings.append("no active RDMA ports found")
    if len(layers) > 1:
        warnings.append("mixed link layers: " + ", ".join(sorted(layers)))
    mems = [n["mem_gib"] for n in nodes if isinstance(n.get("mem_gib"), int | float)]
    if mems and max(mems) - min(mems) > MEM_SPREAD_WARN * max(mems):
        known = ", ".join(
            f"{n.get('hostname')} {n['mem_gib']}"
            for n in nodes
            if isinstance(n.get("mem_gib"), int | float)
        )
        warnings.append(f"node memory differs by more than 5% (GiB): {known}")
    pairs = sorted(layout, key=lambda n: (layout[n]["core"], _natural_key(n)))
    return {
        "nodes": [str(n.get("hostname")) for n in nodes],
        "ppn": len(pairs),
        "nics": pairs,
        "cpus": format_cpus(layout[n]["core"] for n in pairs),
        "link_layer": (
            next(iter(layers)) if len(layers) == 1 else ("mixed" if len(layers) > 1 else None)
        ),
        "per_card": per_card,
        "per_nic": {n: layout[n] for n in pairs},
        "mem_gib": min(mems) if mems else None,
        "warnings": warnings,
    }


# --- text output ------------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def render_table(nodes: list[dict[str, Any]], profile: Mapping[str, Any]) -> str:
    """Readable summary: NIC table (first node), suggested config values, warnings."""
    lines: list[str] = []
    names = ", ".join(profile["nodes"]) or "none"
    lines.append(f"Nodes ({len(profile['nodes'])}): {names}")
    lines.append(f"Memory (smallest node): {_fmt(profile['mem_gib'])} GiB")
    header = ("NIC", "PCI", "NUMA", "core", "link layer", "rate", "netdev", "netdev Mb/s")
    rows: list[tuple[str, ...]] = []
    first_ports = _active_ports(nodes[0]) if nodes else {}
    for name, nic in profile["per_nic"].items():
        rows.append(
            (
                name,
                _fmt(nic.get("pci")),
                _fmt(nic["numa"]),
                _fmt(nic["core"]),
                _fmt(first_ports.get(name, {}).get("link_layer")),
                _fmt(nic["rate"]),
                _fmt(nic["netdev"]),
                _fmt(nic["speed_mbps"]),
            )
        )
    if rows:
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
        lines.append("")
        lines.append(f"ppn: {profile['ppn']}")
        lines.append(f'cpus: "{profile["cpus"]}"')
        lines.append("nics: " + json.dumps(profile["nics"]))
    topo = nodes[0].get("gpu_topo") if nodes else None
    if topo:
        lines.append("")
        lines.append(f"GPU topology ({profile['nodes'][0]}):")
        lines.append(topo.rstrip())
    if profile["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in profile["warnings"])
    return "\n".join(lines) + "\n"


# --- CLI --------------------------------------------------------------------------------------


def _read_merge_input(files: list[str]) -> list[str]:
    lines: list[str] = []
    for name in files:
        if name == "-":
            lines.extend(sys.stdin.read().splitlines())
        else:
            lines.extend(Path(name).read_text().splitlines())
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe RDMA NICs and their placement.")
    parser.add_argument("--sysfs", default="/sys", help="sysfs root (default /sys)")
    parser.add_argument("--proc", default="/proc", help="proc root (default /proc)")
    parser.add_argument("--no-gpu", action="store_true", help="skip nvidia-smi topo -m")
    parser.add_argument("--json", action="store_true", help="one JSON line for this node")
    parser.add_argument(
        "--merge",
        nargs="+",
        metavar="FILE",
        help="merge per-node JSON lines (FILE or - for stdin) into a cluster profile",
    )
    parser.add_argument("--out", help="with --merge: write the profile JSON here, not to stdout")
    parser.add_argument(
        "--per-card",
        action="store_true",
        help="with --merge: one NIC per physical card (devices grouped by PCI slot)",
    )
    args = parser.parse_args(argv)
    if args.per_card and not args.merge:
        parser.error("--per-card needs --merge")

    if args.merge:
        try:
            lines = _read_merge_input(args.merge)
        except OSError as exc:
            print(f"probe: {exc}", file=sys.stderr)
            return 1
        nodes, junk = parse_node_lines(lines)
        try:
            profile = build_profile(nodes, junk_lines=junk, per_card=args.per_card)
        except ValueError as exc:
            print(f"probe: {exc}", file=sys.stderr)
            return 1
        table = render_table(nodes, profile)
        text = json.dumps(profile, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(text)
            sys.stdout.write(table)
        else:
            sys.stderr.write(table)
            sys.stdout.write(text)
        return 0

    node = collect_node(Path(args.sysfs), with_gpu=not args.no_gpu, proc=Path(args.proc))
    if args.json:
        sys.stdout.write(json.dumps(node, separators=(",", ":")) + "\n")
        return 0
    sys.stdout.write(render_table([node], build_profile([node])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
