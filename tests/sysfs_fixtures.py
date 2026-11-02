"""A fake `/sys` tree for the probe and node-check tests.

`Sysfs` writes the files the probe reads (PCI functions linked into `devices/`, RDMA ports, NUMA and
CPU lists); `four_by_four` is a node with four GPUs and four NICs. Synthetic, not captured from a machine.
"""

from __future__ import annotations

import os
from pathlib import Path


class Sysfs:
    """A fake `/sys` tree under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "devices").mkdir(parents=True)
        (root / "bus" / "pci" / "devices").mkdir(parents=True)

    def numa_domain(self, number: int, cpulist: str) -> None:
        node = self.root / "devices" / "system" / "node" / f"node{number}"
        node.mkdir(parents=True)
        (node / "cpulist").write_text(cpulist + "\n")

    def cpu(self, number: int, siblings: str) -> None:
        topology = self.root / "devices" / "system" / "cpu" / f"cpu{number}" / "topology"
        topology.mkdir(parents=True)
        (topology / "thread_siblings_list").write_text(siblings + "\n")

    def pci(self, path: list[str], vendor: str, device_class: str, numa: int = 0) -> Path:
        """A PCI function at `path` (root complex first); its parents are created as bridges."""
        base = self.root / "devices" / os.path.join(*path)
        base.mkdir(parents=True, exist_ok=True)
        for name, text in (("vendor", vendor), ("class", device_class), ("numa_node", str(numa))):
            (base / name).write_text(text + "\n")
        os.symlink(base, self.root / "bus" / "pci" / "devices" / path[-1])
        return base

    def gpu(self, path: list[str], numa: int = 0, vendor: str = "0x10de") -> None:
        self.pci(path, vendor, "0x030200", numa)

    def nic(
        self,
        name: str,
        path: list[str],
        numa: int = 0,
        ports: tuple[str, ...] = ("ACTIVE",),
        link_layer: str = "Ethernet",
        rate: str = "400 Gb/sec (4X NDR)",
    ) -> None:
        function = self.pci(path, "0x15b3", "0x020000", numa)
        base = self.root / "class" / "infiniband" / name
        (base / "ports").mkdir(parents=True)
        os.symlink(function, base / "device")
        for number, state in enumerate(ports, start=1):
            port = base / "ports" / str(number)
            port.mkdir()
            (port / "state").write_text(f"4: {state}\n" if state == "ACTIVE" else f"1: {state}\n")
            (port / "rate").write_text(rate + "\n")
            (port / "link_layer").write_text(link_layer + "\n")


def four_by_four(tmp_path: Path) -> Sysfs:
    """4 GPUs and 4 NICs, each pair behind its own switch; two per NUMA domain (Leonardo-like)."""
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-15")
    fs.numa_domain(1, "16-31")
    for k in range(4):
        root = "pci0000:00" if k < 2 else "pci0000:80"
        numa = 0 if k < 2 else 1
        up, down_gpu, down_nic = (
            f"0000:{k + 1:02x}:00.0",
            f"0000:{k + 1:02x}:01.0",
            f"0000:{k + 1:02x}:02.0",
        )
        fs.gpu([root, up, down_gpu, f"0000:{k + 0x10:02x}:00.0"], numa)
        fs.nic(f"mlx5_{k}", [root, up, down_nic, f"0000:{k + 0x20:02x}:00.0"], numa)
    return fs
