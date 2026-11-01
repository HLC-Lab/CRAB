"""Turns one collected node into a preset `topology` block (ADR-043).

Pure: the input is the dict `probe_sysfs.collect_node` returns (or one saved by `crab probe --json`).
The block is checked with `parse_topology` before it is returned, so a probe never prints a block
that the preset loader would refuse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from crab.core.execution.topology import DISTANCES, parse_topology

PROBE_VERSION = "1"
NIC_ENV = "UCX_NET_DEVICES"
LINK_LAYERS = ("InfiniBand", "Ethernet")


def distance(
    a_path: Sequence[str], b_path: Sequence[str], a_numa: int | None, b_numa: int | None
) -> str | None:
    """The `nvidia-smi topo` class between two PCI devices, from their paths in the PCI tree.

    - `PIX`: behind the same bridge or switch, at the same depth below it.
    - `PXB`: behind the same root port, but through more than one bridge.
    - `PHB`: only the root complex is shared.
    - `NODE`: different root complexes in one NUMA domain; `SYS`: in different domains.
    None when a path or a needed NUMA id is unknown (a VM can hide the PCI tree).
    This is an approximation of what the tools compute, checked against real captures.
    """
    if not a_path or not b_path:
        return None
    common = 0
    for a, b in zip(a_path, b_path, strict=False):
        if a != b:
            break
        common += 1
    if common == 0:
        if a_numa is None or b_numa is None:
            return None
        return "NODE" if a_numa == b_numa else "SYS"
    if common == 1:
        return "PHB"
    below_a, below_b = len(a_path) - common - 1, len(b_path) - common - 1
    return "PIX" if below_a == below_b and below_a <= 1 else "PXB"


def _card(pci: str | None) -> str | None:
    """The PCI slot without the function: `0000:19:00.0` gives `19:00`."""
    parts = (pci or "").split(":")
    return f"{parts[-2]}:{parts[-1].split('.')[0]}" if len(parts) >= 3 else None


def _numa_of(device: Mapping[str, Any], domains: Mapping[str, str]) -> int | None:
    """The device's NUMA domain; -1 or missing means one domain only when the node has just one."""
    numa = device.get("numa")
    if isinstance(numa, int) and numa >= 0:
        return numa
    return int(next(iter(domains))) if len(domains) == 1 else None


def _nearest_gpu(
    nic: Mapping[str, Any], gpus: Sequence[Mapping[str, Any]], numa: int | None
) -> tuple[int, str] | None:
    """(GPU index, distance) of the closest GPU: smallest class, then the lowest PCI address."""
    best: tuple[int, int, str] | None = None
    for index, gpu in enumerate(gpus):
        found = distance(nic["pci_path"], gpu["pci_path"], numa, gpu.get("numa_resolved"))
        if found is not None and (best is None or DISTANCES.index(found) < best[0]):
            best = (DISTANCES.index(found), index, found)
    return None if best is None else (best[1], best[2])


def build_topology(node: Mapping[str, Any], today: str) -> tuple[dict[str, Any], list[str]]:
    """The `topology` block for `node` and the warnings about what could not be read.

    Raises:
        ValueError: the probe built a block that `parse_topology` refuses (a bug or odd data).
    """
    warnings: list[str] = []
    domains = {k: v for k, v in node["numa_cpulists"].items() if v}
    gpus = [dict(g) for g in node["gpus"]]
    counts = {v: sum(g["vendor"] == v for g in gpus) for v in sorted({g["vendor"] for g in gpus})}
    vendors = [max(counts, key=lambda v: counts[v])] if counts else []
    if len(counts) > 1:
        left_out = [g["pci"] for g in gpus if g["vendor"] != vendors[0]]
        listing = ", ".join(f"{v} {n}" for v, n in counts.items())
        warnings.append(
            f"GPUs of several vendors ({listing}): kept the {vendors[0]} ones, "
            f"left out {', '.join(left_out)}"
        )
        gpus = [g for g in gpus if g["vendor"] == vendors[0]]
    for gpu in gpus:
        gpu["numa_resolved"] = _numa_of(gpu, domains)
    block_gpus = [
        {k: v for k, v in (("numa", g["numa_resolved"]), ("pci", g["pci"])) if v is not None}
        for g in gpus
    ]
    block_nics: list[dict[str, Any]] = []
    for nic in node["nics"]:
        if "ACTIVE" not in (nic["state"] or ""):
            warnings.append(f"{nic['dev']} is not active ({nic['state']}): left out")
            continue
        numa = _numa_of(nic, domains)
        entry: dict[str, Any] = {"dev": nic["dev"], "numa": numa, "pci": nic["pci"]}
        entry["card"] = _card(nic["pci"])
        entry["rate_mbps"] = nic["rate_mbps"]
        if nic["link_layer"] in LINK_LAYERS:
            entry["link_layer"] = nic["link_layer"]
        elif nic["link_layer"]:
            warnings.append(f"{nic['dev']}: link layer {nic['link_layer']!r} is not recorded")
        near = _nearest_gpu(nic, gpus, numa)
        if near is not None:
            entry["near_gpu"], entry["dist"] = near
        block_nics.append({k: v for k, v in entry.items() if v is not None})
    if gpus and block_nics and not any("dist" in n for n in block_nics):
        warnings.append("the PCI tree is not visible: no GPU to NIC distances")
    if not gpus:
        warnings.append("no GPU found")
    if not block_nics:
        warnings.append("no active RDMA port found")
    block: dict[str, Any] = {
        "gpu_vendor": vendors[0] if vendors else "none",
        "numa": dict(domains),
        "gpus": block_gpus,
        "nics": block_nics,
        "source": {"kind": "probe", "version": PROBE_VERSION, "date": today},
    }
    if block_nics:
        block["nic_env"] = NIC_ENV
    try:
        parse_topology(block, "probe")
    except ValueError as error:
        raise ValueError(f"the probe built an invalid topology block: {error}") from error
    return block, warnings
