"""`crab probe`: describe this node's GPUs and NICs as a preset `topology` block (ADR-043).

Reads Linux sysfs only. Run it on a compute node (inside an allocation, not on a login node, which
has other hardware), review what it prints and paste the block into a preset override. `--json`
also prints the raw node data, which `--node-file` reads back to rebuild the block elsewhere.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from crab.cli import contract
from crab.core.execution.node_check import Used, allowed_cores, check_node, visible_gpu_count
from crab.core.execution.probe_sysfs import collect_node
from crab.core.execution.probe_topology import build_topology
from crab.core.execution.topology import Topology, parse_topology


def _load_node(path: str) -> dict[str, Any]:
    """The node dict from a file saved with `crab probe --json` (or a bare node dict)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    node = data.get("node", data) if isinstance(data, dict) else None
    if not isinstance(node, dict) or not {"gpus", "nics", "numa_cpulists"} <= set(node):
        raise ValueError(f"{path}: not a node saved by `crab probe --json`")
    return node


def _human(result: dict[str, Any]) -> None:
    node, block = result["node"], result["topology"]
    print(f"Node {node['hostname']}")
    print(f"GPUs ({len(block['gpus'])}):")
    for index, gpu in enumerate(block["gpus"]):
        print(
            f"  {index}  {block['gpu_vendor']}  numa {gpu.get('numa', '?')}  {gpu.get('pci', '?')}"
        )
    print(f"NIC ports ({len(block['nics'])}):")
    for nic in block["nics"]:
        near = f"gpu {nic['near_gpu']} {nic['dist']}" if "near_gpu" in nic else "no GPU distance"
        rate = f"{nic['rate_mbps'] // 1000} Gb/s" if "rate_mbps" in nic else "rate ?"
        print(
            f"  {nic['dev']:<12} numa {nic.get('numa', '?')}  card {nic.get('card', '?'):<6} {rate:<9} {near}"
        )
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    print("\ntopology block (review it, then put it in a preset):")
    print(json.dumps(block, indent=2))


def _read_node(args: argparse.Namespace) -> dict[str, Any]:
    if args.node_file:
        return _load_node(args.node_file)
    if not Path(args.sysfs).is_dir():
        raise ValueError(f"no sysfs at {args.sysfs}: crab probe reads Linux sysfs")
    return collect_node(Path(args.sysfs))


def _expected_topology(args: argparse.Namespace) -> Topology:
    """The topology to check against: a preset's, or a block (or `--json` document) from a file."""
    if args.topology_file:
        try:
            data = json.loads(Path(args.topology_file).read_text())
        except (OSError, ValueError) as error:
            raise ValueError(f"cannot read {args.topology_file}: {error}") from error
        block = data.get("topology", data) if isinstance(data, dict) else data
        return parse_topology(block, f"file {args.topology_file}")
    if not args.preset:
        raise ValueError("--check needs a preset (-p) or a topology file (--topology-file)")
    from crab import paths
    from crab.cli.presets import load_all_presets

    presets = load_all_presets(Path(paths.crab_home()))
    if args.preset not in presets:
        raise ValueError(f"no preset {args.preset!r}; try one of: {', '.join(sorted(presets))}")
    body = presets[args.preset]
    if "topology" not in body:
        raise ValueError(
            f"preset {args.preset!r} has no topology (crab probe prints one for a node)"
        )
    return parse_topology(body["topology"], f"preset {args.preset!r}")


def _check(args: argparse.Namespace) -> None:
    """`--check`: does this node match the topology? Exits 1 and names each thing that does not."""
    try:
        topology = _expected_topology(args)
        node = _read_node(args)
    except ValueError as error:
        print(f"crab: {error}", file=sys.stderr)
        sys.exit(1)
    live = not args.node_file
    used = Used(frozenset(n.dev for n in topology.nics), bool(topology.gpus), frozenset())
    visible = visible_gpu_count() if live and topology.gpus else None
    problems = check_node(node, topology, used, visible, allowed_cores() if live else None)

    def human(result: dict[str, Any]) -> None:
        if result["ok"]:
            print(
                f"ok: this node matches the topology ({len(topology.nics)} NIC ports, {len(topology.gpus)} GPUs)"
            )
        for problem in result["problems"]:
            print(f"problem: {problem}")

    contract.emit({"ok": not problems, "problems": problems}, args.json, human)
    if problems:
        sys.exit(1)


def handle_probe(args: argparse.Namespace) -> None:
    if args.check:
        _check(args)
        return
    try:
        node = _read_node(args)
        block, warnings = build_topology(node, datetime.date.today().isoformat())
    except ValueError as error:
        print(f"crab: {error}", file=sys.stderr)
        sys.exit(1)
    contract.emit({"node": node, "topology": block, "warnings": warnings}, args.json, _human)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "probe", help="Describe this node's GPUs and NICs as a preset topology block"
    )
    parser.add_argument("--sysfs", default="/sys", help="sysfs root to read (default /sys).")
    parser.add_argument(
        "--node-file",
        help="Rebuild the block from a file saved with `crab probe --json` instead of reading sysfs.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check this node against a topology instead of describing it; exit 1 on any mismatch.",
    )
    parser.add_argument(
        "-p", "--preset", help="With --check: the preset whose topology to check against."
    )
    parser.add_argument(
        "--topology-file",
        help="With --check: a topology block, or a `crab probe --json` file, to check against.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of text."
    )
    parser.set_defaults(func=handle_probe)
