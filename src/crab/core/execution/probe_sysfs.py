"""Reads one node's GPUs, RDMA NIC ports and NUMA layout from Linux sysfs (ADR-043).

No vendor tool is called: everything comes from files under `/sys` (or a copy of that tree), and a
missing file is recorded as `None`. The result is a JSON-ready dict that `probe_topology` turns into a
preset `topology` block. Hostnames stay in this dict and never go into a preset.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Any

GPU_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def _read(path: Path) -> str | None:
    """Stripped text of a sysfs file, or None when it is missing or unreadable."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _list_dir(path: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in path.iterdir())
    except OSError:
        return []


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _pci_path(link: Path, sysfs: Path) -> list[str]:
    """The PCI tree path of a device, from its root complex down to itself.

    `/sys/devices/pci0000:00/0000:00:03.1/0000:19:00.0` gives `["pci0000:00", "0000:00:03.1",
    "0000:19:00.0"]`. Empty when the link does not lead into `devices/`.
    """
    real = os.path.realpath(link)
    root = os.path.realpath(sysfs / "devices")
    if not real.startswith(root + os.sep):
        return []
    return os.path.relpath(real, root).split(os.sep)


def _rate_mbps(rate: str | None) -> int | None:
    """Mb/s of a port rate such as "400 Gb/sec (4X NDR)"."""
    match = re.match(r"\s*([\d.]+)\s*([GMT]?)b/sec", rate or "")
    if not match:
        return None
    unit = {"": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6}[match.group(2)]
    try:
        return round(float(match.group(1)) * unit)
    except ValueError:
        return None


def _is_gpu(vendor: str | None, device_class: str | None) -> bool:
    """A display or 3D controller of a known vendor; an Intel VGA device is an integrated GPU."""
    if vendor not in GPU_VENDORS or device_class is None or not device_class.startswith("0x03"):
        return False
    return not (GPU_VENDORS[vendor] == "intel" and device_class.startswith("0x0300"))


def _collect_gpus(sysfs: Path) -> list[dict[str, Any]]:
    root = sysfs / "bus" / "pci" / "devices"
    gpus = []
    for address in sorted(_list_dir(root)):
        base = root / address
        vendor = _read(base / "vendor")
        if _is_gpu(vendor, _read(base / "class")):
            gpus.append(
                {
                    "pci": address,
                    "vendor": GPU_VENDORS[str(vendor)],
                    "numa": _read_int(base / "numa_node"),
                    "pci_path": _pci_path(base, sysfs),
                }
            )
    return gpus


def _collect_nics(sysfs: Path) -> list[dict[str, Any]]:
    """One entry per RDMA port, with the placement of the PCI function it belongs to."""
    root = sysfs / "class" / "infiniband"
    nics = []
    for name in sorted(_list_dir(root), key=_natural_key):
        base = root / name
        pci_path = _pci_path(base / "device", sysfs)
        for port in sorted(_list_dir(base / "ports"), key=_natural_key):
            port_dir = base / "ports" / port
            nics.append(
                {
                    "dev": f"{name}:{port}",
                    "state": _read(port_dir / "state"),
                    "rate_mbps": _rate_mbps(_read(port_dir / "rate")),
                    "link_layer": _read(port_dir / "link_layer"),
                    "pci": pci_path[-1] if pci_path else None,
                    "numa": _read_int(base / "device" / "numa_node"),
                    "pci_path": pci_path,
                }
            )
    return nics


def _collect_numa(sysfs: Path) -> dict[str, str | None]:
    root = sysfs / "devices" / "system" / "node"
    return {
        entry[4:]: _read(root / entry / "cpulist")
        for entry in sorted(_list_dir(root), key=_natural_key)
        if re.fullmatch(r"node\d+", entry)
    }


def collect_node(sysfs: Path, hostname: str | None = None) -> dict[str, Any]:
    """Describe this node as a JSON-ready dict: `gpus`, `nics` (one per port) and `numa_cpulists`."""
    return {
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "gpus": _collect_gpus(sysfs),
        "nics": _collect_nics(sysfs),
        "numa_cpulists": _collect_numa(sysfs),
    }
