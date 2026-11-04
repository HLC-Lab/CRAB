"""Checks a node against a preset's topology, and nodes against each other (ADR-043).

A placement trusts the preset's `topology`. Facts go stale (a port goes down, a node loses a GPU, the job
holds fewer GPUs than the node has), and UCX and CUDA only warn about it, so this fails instead: it names
each thing that does not hold. Pure apart from `visible_gpu_count` and `allowed_cores`, which read the
machine, and `main`, which prints one JSON line for a node.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crab.core.execution.probe_sysfs import DEFAULT_SYSFS, collect_node
from crab.core.execution.topology import Topology, cores_text, parse_core_list, parse_topology

NVIDIA_SMI_TIMEOUT_S = 20


@dataclass(frozen=True)
class Used:
    """What a placement relies on: the NIC ports it names, whether it sets GPUs, the cores it binds.

    `needed_by` maps a port to the `(app key, local rank)` pairs that hold it, so a problem with the port
    can name them; a port missing from it is reported without that suffix.
    """

    nics: frozenset[str] = frozenset()
    gpus: bool = False
    cores: frozenset[int] = frozenset()
    needed_by: Mapping[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)


def needed_by_text(needers: Sequence[tuple[str, int]]) -> str:
    """`app 0 local ranks 1, 3 and app 1 local rank 0`: one phrase per app, in the order given."""
    ranks_of: dict[str, list[int]] = {}
    for key, rank in needers:
        ranks_of.setdefault(key, []).append(rank)
    phrases = [
        f"app {key} local {'rank' if len(ranks) == 1 else 'ranks'} {', '.join(map(str, ranks))}"
        for key, ranks in ranks_of.items()
    ]
    return " and ".join(phrases)


def _suffix(used: Used, dev: str) -> str:
    needers = used.needed_by.get(dev)
    return f", needed by {needed_by_text(needers)}" if needers else ""


def count_gpu_lines(listing: str) -> int:
    """The GPUs in `nvidia-smi -L` output: one line `GPU <n>: ...` each."""
    return sum(1 for line in listing.splitlines() if re.match(r"GPU \d+:", line))


def visible_gpu_count() -> int | None:
    """The GPUs this process can see (what a Slurm cgroup lets through), None without `nvidia-smi`."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, "-L"], capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return count_gpu_lines(done.stdout) if done.returncode == 0 else None


def notes_for(used: Used, visible_gpus: int | None) -> list[str]:
    """What the node could not check, as sentences: not a problem, only worth recording.

    A node that sets GPUs but could not count the visible ones (no `nvidia-smi`, a timeout, a failing
    exit) skips the visible-GPU check silently otherwise; on AMD or Intel nodes that is normal.
    """
    if used.gpus and visible_gpus is None:
        return ["visible GPUs not verified: nvidia-smi gave no count"]
    return []


def allowed_cores(proc: Path = Path("/proc")) -> frozenset[int] | None:
    """The cores this process may run on (`Cpus_allowed_list`), None when it cannot be read."""
    try:
        text = (proc / "self" / "status").read_text()
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"^Cpus_allowed_list:\s*(\S+)", text, re.MULTILINE)
    if match is None:
        return None
    try:
        return frozenset(parse_core_list(match.group(1), "status"))
    except ValueError:
        return None


def _numa_of(node: Mapping[str, Any]) -> dict[int, tuple[int, ...]]:
    found: dict[int, tuple[int, ...]] = {}
    for key, text in node["numa_cpulists"].items():
        try:
            found[int(key)] = parse_core_list(text, "node")
        except ValueError:
            continue
    return found


def check_node(
    node: Mapping[str, Any],
    topology: Topology,
    used: Used,
    visible_gpus: int | None = None,
    allowed: frozenset[int] | None = None,
) -> list[str]:
    """What does not hold on this node, as sentences; empty when everything does.

    `node` is `probe_sysfs.collect_node`'s result; `visible_gpus` and `allowed` are what
    `visible_gpu_count` and `allowed_cores` read (None skips that check).
    """
    problems: list[str] = []
    ports = {nic["dev"]: nic for nic in node["nics"]}
    expected = {nic.dev: nic for nic in topology.nics}
    for dev in sorted(used.nics):
        port = ports.get(dev)
        needed = _suffix(used, dev)
        if port is None:
            problems.append(f"{dev} is not on this node{needed}")
            continue
        if "ACTIVE" not in (port["state"] or ""):
            problems.append(f"{dev} is not active ({port['state']}){needed}")
        want = expected[dev].pci if dev in expected else None
        if want is not None and port["pci"] != want:
            problems.append(f"{dev} is at PCI {port['pci']}, the topology says {want}{needed}")
    if used.gpus:
        gpus = node["gpus"]
        if len(gpus) != len(topology.gpus):
            problems.append(
                f"the node has {len(gpus)} GPUs, the topology lists {len(topology.gpus)}"
            )
        else:
            for index, (found, listed) in enumerate(zip(gpus, topology.gpus, strict=True)):
                if listed.pci is not None and found["pci"] != listed.pci:
                    problems.append(
                        f"GPU {index} is at PCI {found['pci']}, the topology says {listed.pci}"
                    )
        if visible_gpus is not None and visible_gpus != len(topology.gpus):
            problems.append(
                f"{visible_gpus} GPUs are visible to the job, the topology lists {len(topology.gpus)}; "
                "bind.gpu needs the job to see all of them (for example --gres=gpu:"
                f"{len(topology.gpus)})"
            )
    if used.cores and allowed is not None and not used.cores <= allowed:
        missing = sorted(used.cores - allowed)
        problems.append(f"cores {cores_text(tuple(missing))} are not allowed to the job")
    if topology.numa and _numa_of(node) != {d: tuple(c) for d, c in topology.numa.items()}:
        problems.append("the node's NUMA domains differ from the topology's")
    return problems


def node_facts(node: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a node every node of an allocation must share: NIC ports by PCI slot, GPUs by slot."""
    return {
        "nics": {
            nic["dev"]: nic["pci"] for nic in node["nics"] if "ACTIVE" in (nic["state"] or "")
        },
        "gpus": [gpu["pci"] for gpu in node["gpus"]],
        "numa": {k: v for k, v in sorted(node["numa_cpulists"].items())},
    }


def compare_nodes(facts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """How the nodes differ from the first one (by host name order); empty when they are alike."""
    hosts = sorted(facts)
    problems: list[str] = []
    for host in hosts[1:]:
        base, other = facts[hosts[0]], facts[host]
        if base["nics"] != other["nics"]:
            only_base = sorted(set(base["nics"]) - set(other["nics"]))
            only_other = sorted(set(other["nics"]) - set(base["nics"]))
            moved = sorted(
                d
                for d in base["nics"]
                if d in other["nics"] and base["nics"][d] != other["nics"][d]
            )
            detail = [f"{host} lacks {', '.join(only_base)}"] if only_base else []
            detail += [f"{host} also has {', '.join(only_other)}"] if only_other else []
            detail += [f"{d} is in another PCI slot on {host}" for d in moved]
            problems.append(f"active NIC ports differ from {hosts[0]}: {'; '.join(detail)}")
        if len(base["gpus"]) != len(other["gpus"]):
            problems.append(
                f"{host} has {len(other['gpus'])} GPUs, {hosts[0]} has {len(base['gpus'])}"
            )
        elif base["gpus"] != other["gpus"]:
            problems.append(f"the GPUs of {host} are in other PCI slots than those of {hosts[0]}")
        if base["numa"] != other["numa"]:
            problems.append(f"the NUMA domains of {host} differ from those of {hosts[0]}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m crab.core.execution.node_check SPEC.json`: one JSON line about this node.

    The spec is `{"topology": <topology block>, "nics": [...], "needed_by": {port: [[app key, local rank],
    ...]}, "gpus": bool, "cores": [...]}` (a spec without `needed_by` names no needers); the line is
    `{"host", "problems", "facts"}`, plus `"notes"` when the node could not check something.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: node_check SPEC.json", file=sys.stderr)
        return 2
    try:
        spec = json.loads(Path(args[0]).read_text())
        topology = parse_topology(spec["topology"], "node check")
        node = collect_node(Path(DEFAULT_SYSFS))
        needed_by = {
            port: tuple((str(key), int(rank)) for key, rank in needers)
            for port, needers in spec.get("needed_by", {}).items()
        }
        used = Used(
            frozenset(spec.get("nics", [])),
            bool(spec.get("gpus")),
            frozenset(spec.get("cores", [])),
            needed_by,
        )
        gpus = visible_gpu_count() if used.gpus else None
        problems = check_node(node, topology, used, gpus, allowed_cores())
        line: dict[str, Any] = {
            "host": socket.gethostname(),
            "problems": problems,
            "facts": node_facts(node),
        }
        notes = notes_for(used, gpus)
        if notes:
            line["notes"] = notes
    except Exception as error:  # a node that cannot even check must say so, not stay silent
        crash = f"the node check crashed: {type(error).__name__}: {error}"
        line = {"host": socket.gethostname(), "problems": [crash], "facts": None}
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
