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
from crab.core.execution.probe_sysfs import collect_node
from crab.core.execution.probe_topology import build_topology


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


def handle_probe(args: argparse.Namespace) -> None:
    try:
        if args.node_file:
            node = _load_node(args.node_file)
        elif not Path(args.sysfs).is_dir():
            raise ValueError(f"no sysfs at {args.sysfs}: crab probe reads Linux sysfs")
        else:
            node = collect_node(Path(args.sysfs))
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
        "--json", action="store_true", help="Emit machine-readable JSON instead of text."
    )
    parser.set_defaults(func=handle_probe)
