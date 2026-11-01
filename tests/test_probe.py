"""`crab probe`: sysfs reading, distance classes, the topology block and the command.

The sysfs trees are built here by `Sysfs` and are synthetic: they follow the real layout (a device
symlink into `devices/<root complex>/<bridge>/.../<function>`), but no capture from a real cluster
is used yet.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from crab.cli import probe
from crab.core.execution.probe_sysfs import collect_node
from crab.core.execution.probe_topology import build_topology, distance
from crab.core.execution.settings import from_preset
from crab.core.execution.topology import parse_topology

TODAY = "2026-11-01"


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


def block(fs: Sysfs) -> tuple[dict[str, Any], list[str]]:
    return build_topology(collect_node(fs.root, hostname="n1"), TODAY)


def test_four_gpus_and_four_nics_each_pair_behind_one_switch(tmp_path: Path) -> None:
    topology, warnings = block(four_by_four(tmp_path))
    assert warnings == []
    assert topology["gpu_vendor"] == "nvidia"
    assert topology["nic_env"] == "UCX_NET_DEVICES"
    assert topology["numa"] == {"0": "0-15", "1": "16-31"}
    assert [g["numa"] for g in topology["gpus"]] == [0, 0, 1, 1]
    assert [(n["dev"], n["near_gpu"], n["dist"], n["numa"]) for n in topology["nics"]] == [
        ("mlx5_0:1", 0, "PIX", 0),
        ("mlx5_1:1", 1, "PIX", 0),
        ("mlx5_2:1", 2, "PIX", 1),
        ("mlx5_3:1", 3, "PIX", 1),
    ]
    assert topology["nics"][0]["rate_mbps"] == 400000
    assert topology["nics"][0]["link_layer"] == "Ethernet"
    assert topology["source"] == {"kind": "probe", "version": "1", "date": TODAY}


def test_the_block_passes_the_preset_loader_and_keeps_no_hostname(tmp_path: Path) -> None:
    topology, _ = block(four_by_four(tmp_path))
    from_preset({"scheduler": "slurm", "topology": topology}, "probed")
    assert "n1" not in json.dumps(topology)


def test_two_ports_of_one_card_share_the_card_and_the_nearest_gpu(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-39,80-119")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:00:02.0", "0000:19:00.0"])
    fs.nic("mlx5_0", ["pci0000:00", "0000:00:01.0", "0000:00:03.0", "0000:1a:00.0"])
    fs.nic("mlx5_1", ["pci0000:00", "0000:00:01.0", "0000:00:03.0", "0000:1a:00.1"])
    topology, _ = block(fs)
    first, second = topology["nics"]
    assert (first["card"], second["card"]) == ("1a:00", "1a:00")
    assert (first["near_gpu"], second["near_gpu"]) == (0, 0)
    assert topology["numa"] == {"0": "0-39,80-119"}


def test_the_nearest_gpu_wins_and_ties_go_to_the_lowest_pci_address(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:10:00.0"])
    fs.gpu(["pci0000:00", "0000:00:02.0", "0000:20:00.0"])
    fs.nic("mlx5_0", ["pci0000:00", "0000:00:02.0", "0000:21:00.0"])
    fs.nic("mlx5_1", ["pci0000:00", "0000:00:03.0", "0000:30:00.0"])
    topology, _ = block(fs)
    assert [(n["near_gpu"], n["dist"]) for n in topology["nics"]] == [(1, "PIX"), (0, "PHB")]


@pytest.mark.parametrize(
    ("a", "b", "a_numa", "b_numa", "expected"),
    [
        (["r", "br", "gpu"], ["r", "br", "nic"], 0, 0, "PIX"),
        (["r", "up", "d1", "gpu"], ["r", "up", "d2", "nic"], 0, 0, "PIX"),
        (["r", "br", "gpu"], ["r", "br", "up", "dn", "nic"], 0, 0, "PXB"),
        (["r", "up", "a", "b", "gpu"], ["r", "up", "c", "d", "nic"], 0, 0, "PXB"),
        (["r", "x", "gpu"], ["r", "y", "nic"], 0, 0, "PHB"),
        (["r1", "gpu"], ["r2", "nic"], 0, 0, "NODE"),
        (["r1", "gpu"], ["r2", "nic"], 0, 1, "SYS"),
        (["r1", "gpu"], ["r2", "nic"], None, 1, None),
        ([], ["r2", "nic"], 0, 0, None),
    ],
)
def test_distance_classes(
    a: list[str], b: list[str], a_numa: Any, b_numa: Any, expected: Any
) -> None:
    assert distance(a, b, a_numa, b_numa) == expected


def test_a_port_that_is_down_and_an_odd_link_layer_are_reported(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    fs.nic("mlx5_4", ["pci0000:80", "0000:09:00.0", "0000:29:00.0"], 1, ports=("DOWN",))
    fs.nic("mlx5_5", ["pci0000:80", "0000:0a:00.0", "0000:2a:00.0"], 1, link_layer="Omni-Path")
    topology, warnings = block(fs)
    assert "mlx5_4:1 is not active (1: DOWN): left out" in warnings
    assert "mlx5_5:1: link layer 'Omni-Path' is not recorded" in warnings
    devs = [n["dev"] for n in topology["nics"]]
    assert "mlx5_4:1" not in devs and "mlx5_5:1" in devs
    assert "link_layer" not in topology["nics"][-1]


def test_cross_domain_nic_is_sys_and_same_domain_other_root_is_node(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.numa_domain(1, "8-15")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:10:00.0"], 0)
    fs.nic("mlx5_0", ["pci0000:80", "0000:80:01.0", "0000:90:00.0"], 1)
    fs.nic("mlx5_1", ["pci0000:40", "0000:40:01.0", "0000:50:00.0"], 0)
    topology, _ = block(fs)
    assert [n["dist"] for n in topology["nics"]] == ["SYS", "NODE"]


def test_a_node_without_gpus_still_lists_its_nics(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.nic("mlx5_0", ["pci0000:00", "0000:00:01.0", "0000:10:00.0"])
    topology, warnings = block(fs)
    assert topology["gpu_vendor"] == "none" and topology["gpus"] == []
    assert "near_gpu" not in topology["nics"][0]
    assert "no GPU found" in warnings


def test_a_node_without_active_nics_has_no_nic_env(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:10:00.0"])
    topology, warnings = block(fs)
    assert "nic_env" not in topology and topology["nics"] == []
    assert "no active RDMA port found" in warnings


def test_a_hidden_pci_tree_gives_no_distances_and_a_warning(tmp_path: Path) -> None:
    """Readable attributes, but every device link leads outside `devices/` (a remapped sysfs)."""
    fs = four_by_four(tmp_path)
    outside = tmp_path / "outside"
    for link in sorted((tmp_path / "bus" / "pci" / "devices").iterdir()):
        copy = outside / link.name
        shutil.copytree(link.resolve(), copy, ignore=shutil.ignore_patterns("0000:*"))
        link.unlink()
        os.symlink(copy, link)
    for device in (tmp_path / "class" / "infiniband").iterdir():
        function = (device / "device").resolve().name
        (device / "device").unlink()
        os.symlink(outside / function, device / "device")
    topology, warnings = block(fs)
    assert len(topology["gpus"]) == 4 and len(topology["nics"]) == 4
    assert not any("dist" in n for n in topology["nics"])
    assert "the PCI tree is not visible: no GPU to NIC distances" in warnings


def test_numa_node_minus_one_means_the_only_domain(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:10:00.0"], numa=-1)
    topology, _ = block(fs)
    assert topology["gpus"][0]["numa"] == 0


def test_only_real_gpus_count(tmp_path: Path) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.pci(["pci0000:00", "0000:00:02.0"], "0x8086", "0x030000")  # integrated Intel graphics
    fs.pci(["pci0000:00", "0000:00:03.0"], "0x1a03", "0x030000")  # board management VGA
    fs.pci(["pci0000:00", "0000:00:04.0"], "0x10de", "0x040300")  # NVIDIA audio function
    fs.pci(["pci0000:00", "0000:00:05.0"], "0x1002", "0x038000")  # AMD accelerator
    topology, _ = block(fs)
    assert topology["gpu_vendor"] == "amd" and len(topology["gpus"]) == 1


def test_gpus_of_several_vendors_keep_the_majority_and_name_what_was_left_out(
    tmp_path: Path,
) -> None:
    fs = Sysfs(tmp_path)
    fs.numa_domain(0, "0-7")
    fs.gpu(["pci0000:00", "0000:00:01.0", "0000:01:00.0"], vendor="0x10de")
    fs.gpu(["pci0000:00", "0000:00:02.0", "0000:05:00.0"], vendor="0x1002")
    fs.gpu(["pci0000:00", "0000:00:03.0", "0000:06:00.0"], vendor="0x1002")
    fs.nic("mlx5_0", ["pci0000:00", "0000:00:03.0", "0000:07:00.0"])
    topology, warnings = block(fs)
    assert topology["gpu_vendor"] == "amd"
    assert [g["pci"] for g in topology["gpus"]] == ["0000:05:00.0", "0000:06:00.0"]
    assert topology["nics"][0]["near_gpu"] == 1
    assert warnings == [
        "GPUs of several vendors (amd 2, nvidia 1): kept the amd ones, left out 0000:01:00.0"
    ]


def test_an_unreadable_sysfs_gives_an_empty_node_not_a_crash(tmp_path: Path) -> None:
    node = collect_node(tmp_path, hostname="n1")
    assert node == {"hostname": "n1", "gpus": [], "nics": [], "numa_cpulists": {}}
    topology, warnings = build_topology(node, TODAY)
    assert parse_topology(topology, "probe").gpus == ()
    assert "no GPU found" in warnings


def run_probe(capsys: pytest.CaptureFixture[str], **options: Any) -> tuple[str, str, int]:
    args = argparse.Namespace(**{"sysfs": "/sys", "node_file": None, "json": False, **options})
    try:
        probe.handle_probe(args)
        code = 0
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    captured = capsys.readouterr()
    return captured.out, captured.err, code


def test_the_command_prints_json_with_node_block_and_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    four_by_four(tmp_path)
    out, _, code = run_probe(capsys, sysfs=str(tmp_path), json=True)
    data = json.loads(out)
    assert code == 0 and set(data) == {"node", "topology", "warnings"}
    assert len(data["topology"]["nics"]) == 4 and data["node"]["gpus"][0]["vendor"] == "nvidia"


def test_the_human_output_lists_gpus_nics_and_the_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    four_by_four(tmp_path)
    out, _, _ = run_probe(capsys, sysfs=str(tmp_path))
    assert "GPUs (4):" in out and "NIC ports (4):" in out
    assert "mlx5_2:1" in out and "gpu 2 PIX" in out and '"gpu_vendor": "nvidia"' in out


def test_a_saved_node_rebuilds_the_same_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    four_by_four(tmp_path / "sys")
    out, _, _ = run_probe(capsys, sysfs=str(tmp_path / "sys"), json=True)
    saved = tmp_path / "node.json"
    saved.write_text(out)
    again, _, code = run_probe(capsys, node_file=str(saved), json=True)
    assert code == 0 and json.loads(again)["topology"] == json.loads(out)["topology"]


def test_errors_are_one_line_and_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, err, code = run_probe(capsys, sysfs=str(tmp_path / "nope"))
    assert code == 1 and err.startswith("crab: no sysfs at ") and "reads Linux sysfs" in err
    junk = tmp_path / "junk.json"
    junk.write_text("[1, 2]")
    _, err, code = run_probe(capsys, node_file=str(junk))
    assert code == 1 and "not a node saved by `crab probe --json`" in err
    _, err, code = run_probe(capsys, node_file=str(tmp_path / "missing.json"))
    assert code == 1 and err.startswith("crab: cannot read ")


def test_the_command_is_registered_with_its_options() -> None:
    parser = argparse.ArgumentParser()
    probe.register(parser.add_subparsers())
    args = parser.parse_args(["probe", "--json", "--sysfs", "/x", "--node-file", "n.json"])
    assert (args.json, args.sysfs, args.node_file, args.func) == (
        True,
        "/x",
        "n.json",
        probe.handle_probe,
    )
