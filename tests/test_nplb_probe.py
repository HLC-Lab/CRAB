"""examples/nplb/probe.py: per-node RDMA NIC probe and cluster-profile merge, against fake sysfs
trees in tmp_path. Nothing here reads the real /sys or runs nvidia-smi."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

PROBE_PATH = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "probe.py"
RATE = "400 Gb/sec (4X NDR)"


def _load():
    spec = importlib.util.spec_from_file_location("nplb_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["nplb_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


def make_nic(
    root: Path,
    dev: str,
    *,
    numa: int | None = 0,
    cpulist: str | None = "0-31",
    ports: tuple[tuple[int, str, str], ...] = ((1, "4: ACTIVE", "Ethernet"),),
    rate: str = RATE,
    netdev: str | None = None,
    speed: str | None = "400000",
    pci: str | None = None,
) -> None:
    base = root / "class" / "infiniband" / dev
    if pci is not None and not (base / "device").is_symlink():
        # as on a real node: <dev>/device is a symlink to the PCI device directory
        target = root / "devices" / "pci0000:00" / pci
        target.mkdir(parents=True, exist_ok=True)
        base.mkdir(parents=True, exist_ok=True)
        (base / "device").symlink_to(target)
    for port, state, layer in ports:
        _write(base / "ports" / str(port) / "state", state)
        _write(base / "ports" / str(port) / "rate", rate)
        _write(base / "ports" / str(port) / "link_layer", layer)
    if numa is not None:
        _write(base / "device" / "numa_node", str(numa))
    if cpulist is not None:
        _write(base / "device" / "local_cpulist", cpulist)
    if netdev is not None:
        (base / "device" / "net" / netdev).mkdir(parents=True)
        if speed is not None:
            _write(root / "class" / "net" / netdev / "speed", speed)


def make_numa(root: Path, nodes: dict[int, str]) -> None:
    for n, cpulist in nodes.items():
        _write(root / "devices" / "system" / "node" / f"node{n}" / "cpulist", cpulist)


def eight_nics(root: Path, numa_of=lambda i: 0 if i < 4 else 1) -> None:
    for i in range(8):
        numa = numa_of(i)
        # a few cores per NUMA node apart, so the lowest free core is deterministic
        make_nic(
            root,
            f"mlx5_{i}",
            numa=numa,
            cpulist="0-31" if numa == 0 else "32-63",
            netdev=f"eth{i}",
        )
    make_numa(root, {0: "0-31", 1: "32-63"})


def profile_of(probe, root: Path, host: str = "node-a", procid: int | None = None):
    environ = {} if procid is None else {"SLURM_PROCID": str(procid), "SLURM_NODEID": str(procid)}
    node = probe.collect_node(root, with_gpu=False, hostname=host, environ=environ)
    return probe.build_profile([node])


def test_eight_nics_two_numa_nodes(probe, tmp_path):
    eight_nics(tmp_path)
    profile = profile_of(probe, tmp_path)
    assert profile["nics"] == [f"mlx5_{i}:1" for i in range(8)]
    assert profile["cpus"] == "0,1,2,3,32,33,34,35"
    assert profile["ppn"] == 8
    assert profile["link_layer"] == "Ethernet"
    assert profile["nodes"] == ["node-a"]
    assert profile["warnings"] == []
    assert profile["per_nic"]["mlx5_4:1"] == {
        "numa": 1,
        "core": 32,
        "rate": RATE,
        "netdev": "eth4",
        "speed_mbps": 400000,
    }


def test_interleaved_numa_orders_nics_by_core(probe, tmp_path):
    eight_nics(tmp_path, numa_of=lambda i: 1 - (i % 2))
    profile = profile_of(probe, tmp_path)
    # mlx5_1,3,5,7 sit on node 0 (cores 0..3), mlx5_0,2,4,6 on node 1 (cores 32..35)
    assert profile["cpus"] == "0,1,2,3,32,33,34,35"
    assert profile["nics"] == [
        "mlx5_1:1",
        "mlx5_3:1",
        "mlx5_5:1",
        "mlx5_7:1",
        "mlx5_0:1",
        "mlx5_2:1",
        "mlx5_4:1",
        "mlx5_6:1",
    ]
    cores = [profile["per_nic"][n]["core"] for n in profile["nics"]]
    assert cores == sorted(cores)


def test_down_port_and_dual_port_device(probe, tmp_path):
    make_nic(tmp_path, "mlx5_0", numa=0, cpulist="0-15", netdev="eth0")
    make_nic(
        tmp_path,
        "mlx5_1",
        numa=0,
        cpulist="0-15",
        ports=((1, "4: ACTIVE", "InfiniBand"), (2, "4: ACTIVE", "InfiniBand")),
    )
    make_nic(tmp_path, "mlx5_2", numa=0, cpulist="0-15", ports=((1, "1: DOWN", "Ethernet"),))
    make_numa(tmp_path, {0: "0-15"})
    profile = profile_of(probe, tmp_path)
    assert profile["nics"] == ["mlx5_0:1", "mlx5_1:1", "mlx5_1:2"]
    assert profile["cpus"] == "0,1,2"
    assert profile["ppn"] == 3
    assert profile["link_layer"] == "mixed"
    joined = "\n".join(profile["warnings"])
    assert "mlx5_2:1" in joined and "DOWN" in joined
    assert "mlx5_1" in joined and "report before continuing" in joined
    assert "mixed link layers" in joined


def test_missing_files_do_not_crash(probe, tmp_path):
    # no numa_node, no local_cpulist, no net dir, no node cpulists
    make_nic(tmp_path, "mlx5_0", numa=None, cpulist=None)
    make_nic(tmp_path, "mlx5_1", numa=None, cpulist=None)
    node = probe.collect_node(tmp_path, with_gpu=False, hostname="node-a", environ={})
    dev = node["devices"]["mlx5_0"]
    assert dev["numa"] is None and dev["cpulist"] is None
    assert dev["netdevs"] == [] or dev["netdevs"] is None
    profile = probe.build_profile([node])
    assert profile["nics"] == ["mlx5_0:1", "mlx5_1:1"]
    assert profile["cpus"] == "0,1"
    assert profile["per_nic"]["mlx5_0:1"]["numa"] is None
    assert profile["per_nic"]["mlx5_0:1"]["netdev"] is None
    assert profile["per_nic"]["mlx5_0:1"]["speed_mbps"] is None
    joined = "\n".join(profile["warnings"])
    assert "NUMA node unknown" in joined


def test_numa_node_cpulist_is_the_fallback(probe, tmp_path):
    make_nic(tmp_path, "mlx5_0", numa=1, cpulist=None)
    make_numa(tmp_path, {0: "0-7", 1: "8-15"})
    profile = profile_of(probe, tmp_path)
    assert profile["cpus"] == "8"
    assert any("local_cpulist" in w for w in profile["warnings"])


def test_unknown_numa_minus_one_warns(probe, tmp_path):
    make_nic(tmp_path, "mlx5_0", numa=-1, cpulist="4-7")
    profile = profile_of(probe, tmp_path)
    assert profile["cpus"] == "4"
    assert profile["per_nic"]["mlx5_0:1"]["numa"] is None
    assert any("NUMA node unknown" in w for w in profile["warnings"])


def test_netdev_speed_differing_from_rate_warns(probe, tmp_path):
    make_nic(tmp_path, "mlx5_0", netdev="eth0", speed="100000")
    profile = profile_of(probe, tmp_path)
    assert any("eth0" in w and "100000" in w for w in profile["warnings"])


def test_no_devices_gives_empty_profile_with_warning(probe, tmp_path):
    profile = profile_of(probe, tmp_path)
    assert profile["nics"] == [] and profile["cpus"] == "" and profile["ppn"] == 0
    assert profile["warnings"]


def test_cores_are_not_reused_between_nics_on_one_numa_node(probe, tmp_path):
    for i in range(3):
        make_nic(tmp_path, f"mlx5_{i}", numa=0, cpulist="0-31")
    profile = profile_of(probe, tmp_path)
    assert profile["cpus"] == "0,1,2"


def _node_line(probe, root: Path, host: str, procid: int | None) -> str:
    environ = {} if procid is None else {"SLURM_PROCID": str(procid), "SLURM_NODEID": str(procid)}
    node = probe.collect_node(root, with_gpu=False, hostname=host, environ=environ)
    return json.dumps(node)


def test_merge_orders_by_procid_counts_junk_and_names_odd_node(probe, tmp_path):
    full = tmp_path / "full"
    eight_nics(full)
    short = tmp_path / "short"
    eight_nics(short)
    shutil.rmtree(short / "class" / "infiniband" / "mlx5_7")
    lines = [
        _node_line(probe, full, "node-c", 2),
        _node_line(probe, full, "node-a", 0),
        "srun: warning: something noisy",
        "",
        _node_line(probe, short, "node-b", 1),
    ]
    nodes, junk = probe.parse_node_lines(lines)
    assert junk == 1
    profile = probe.build_profile(nodes, junk_lines=junk)
    assert profile["nodes"] == ["node-a", "node-b", "node-c"]
    assert profile["ppn"] == 8
    joined = "\n".join(profile["warnings"])
    assert "node-b" in joined and "mlx5_7:1" in joined
    assert "node-c" not in joined
    assert "ignored 1 non-JSON line" in joined


def test_merge_without_procid_keeps_input_order(probe, tmp_path):
    eight_nics(tmp_path)
    lines = [_node_line(probe, tmp_path, h, None) for h in ("node-z", "node-a")]
    nodes, junk = probe.parse_node_lines(lines)
    assert junk == 0
    assert probe.build_profile(nodes)["nodes"] == ["node-z", "node-a"]


def test_merge_warns_on_numa_and_rate_differences(probe, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    eight_nics(a)
    eight_nics(b, numa_of=lambda i: 1 - (i % 2))
    lines = [_node_line(probe, a, "node-a", 0), _node_line(probe, b, "node-b", 1)]
    nodes, _ = probe.parse_node_lines(lines)
    joined = "\n".join(probe.build_profile(nodes)["warnings"])
    assert "node-b" in joined and "NUMA" in joined

    c = tmp_path / "c"
    eight_nics(c)
    make_nic(c, "mlx5_0", numa=0, cpulist="0-31", rate="200 Gb/sec (4X HDR)")
    lines = [_node_line(probe, a, "node-a", 0), _node_line(probe, c, "node-c", 1)]
    nodes, _ = probe.parse_node_lines(lines)
    joined = "\n".join(probe.build_profile(nodes)["warnings"])
    assert "node-c" in joined and "200 Gb/sec" in joined


def test_json_output_is_one_line_and_round_trips_through_merge(
    probe, tmp_path, monkeypatch, capsys
):
    root = tmp_path / "sys"
    eight_nics(root)
    monkeypatch.setattr(probe.socket, "gethostname", lambda: "node-a")
    monkeypatch.setenv("SLURM_PROCID", "0")
    monkeypatch.setenv("SLURM_NODEID", "0")
    assert probe.main(["--json", "--sysfs", str(root), "--no-gpu"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    line = json.loads(out)
    assert line["hostname"] == "node-a" and line["procid"] == 0 and line["nodeid"] == 0
    assert line["gpu_topo"] is None
    assert set(line["devices"]) == {f"mlx5_{i}" for i in range(8)}

    node_file = tmp_path / "probe.jsonl"
    node_file.write_text(out)
    assert probe.main(["--merge", str(node_file)]) == 0
    captured = capsys.readouterr()
    profile = json.loads(captured.out)
    assert profile["nics"] == [f"mlx5_{i}:1" for i in range(8)]
    assert profile["cpus"] == "0,1,2,3,32,33,34,35"
    assert captured.err  # the readable table

    out_file = tmp_path / "profile.json"
    monkeypatch.setattr(sys, "stdin", io.StringIO(out))
    assert probe.main(["--merge", "-", "--out", str(out_file)]) == 0
    assert json.loads(out_file.read_text()) == profile


def test_human_mode_on_a_machine_without_rdma_exits_zero(probe, tmp_path, capsys):
    assert probe.main(["--sysfs", str(tmp_path), "--no-gpu"]) == 0
    out = capsys.readouterr()
    assert "no active RDMA ports" in out.out + out.err


def test_gpu_topo_is_null_without_nvidia_smi(probe, tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)

    def boom(*args, **kwargs):
        raise AssertionError("nvidia-smi must not run")

    monkeypatch.setattr(probe.subprocess, "run", boom)
    node = probe.collect_node(tmp_path, with_gpu=True, hostname="node-a", environ={})
    assert node["gpu_topo"] is None


def test_gpu_topo_failure_is_recorded_not_fatal(probe, tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/fake/nvidia-smi")

    def fail(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(probe.subprocess, "run", fail)
    node = probe.collect_node(tmp_path, with_gpu=True, hostname="node-a", environ={})
    assert "exec format error" in node["gpu_topo"]


def test_cpulist_helpers(probe):
    assert probe.parse_cpulist("0-3,8,10-11") == [0, 1, 2, 3, 8, 10, 11]
    assert probe.parse_cpulist("") == []
    assert probe.parse_cpulist(None) == []
    assert probe.parse_cpulist("garbage") == []


def make_meminfo(proc: Path, kb: int) -> Path:
    _write(proc / "meminfo", f"MemTotal:  {kb} kB\nMemFree:   1024 kB")
    return proc


def test_mem_gib_from_meminfo(probe, tmp_path):
    proc = make_meminfo(tmp_path / "proc", 2113929216)
    node = probe.collect_node(tmp_path, with_gpu=False, hostname="node-a", environ={}, proc=proc)
    assert node["mem_gib"] == 2016.0
    assert probe.build_profile([node])["mem_gib"] == 2016.0


def test_missing_meminfo_is_null(probe, tmp_path):
    node = probe.collect_node(
        tmp_path, with_gpu=False, hostname="node-a", environ={}, proc=tmp_path / "no-proc"
    )
    assert node["mem_gib"] is None
    profile = probe.build_profile([node])
    assert profile["mem_gib"] is None
    assert not any("memory" in w for w in profile["warnings"])


def test_merge_takes_smallest_memory_and_warns_on_a_large_difference(probe, tmp_path):
    lines = []
    for host, procid, kb in (("node-a", 0, 2113929216), ("node-b", 1, 1056964608)):
        proc = make_meminfo(tmp_path / host, kb)
        node = probe.collect_node(
            tmp_path,
            with_gpu=False,
            hostname=host,
            environ={"SLURM_PROCID": str(procid)},
            proc=proc,
        )
        lines.append(json.dumps(node))
    nodes, _ = probe.parse_node_lines(lines)
    profile = probe.build_profile(nodes)
    assert profile["mem_gib"] == 1008.0
    assert any("memory differs" in w and "node-b" in w for w in profile["warnings"])


def test_merge_does_not_warn_on_a_small_memory_difference(probe):
    nodes = [
        {"hostname": "node-a", "procid": 0, "devices": {}, "mem_gib": 2016.0},
        {"hostname": "node-b", "procid": 1, "devices": {}, "mem_gib": 2000.0},
    ]
    profile = probe.build_profile(nodes)
    assert profile["mem_gib"] == 2000.0
    assert not any("memory" in w for w in profile["warnings"])


def test_json_line_and_table_carry_memory(probe, tmp_path, monkeypatch, capsys):
    proc = make_meminfo(tmp_path / "proc", 2113929216)
    args = ["--sysfs", str(tmp_path), "--proc", str(proc), "--no-gpu"]
    assert probe.main(["--json", *args]) == 0
    assert json.loads(capsys.readouterr().out)["mem_gib"] == 2016.0
    assert probe.main(args) == 0
    assert "2016.0 GiB" in capsys.readouterr().out


CARDS = ("0000:19:00", "0000:3b:00", "0000:98:00", "0000:bb:00")


def card_nics(
    root: Path,
    down: tuple[str, ...] = (),
    dead_cards: tuple[int, ...] = (),
    with_pci: bool = True,
) -> None:
    """4 dual-function cards as 8 devices: mlx5_{2c} and mlx5_{2c+1} are functions 0 and 1 of card
    c; cards 0-1 sit on NUMA node 0, cards 2-3 on node 1. `down` lists devices with a DOWN port,
    `dead_cards` the cards whose two devices are both DOWN."""
    for card, prefix in enumerate(CARDS):
        numa = 0 if card < 2 else 1
        for function in (0, 1):
            dev = f"mlx5_{2 * card + function}"
            is_down = dev in down or card in dead_cards
            make_nic(
                root,
                dev,
                numa=numa,
                cpulist="0-31" if numa == 0 else "32-63",
                ports=((1, "1: DOWN" if is_down else "4: ACTIVE", "Ethernet"),),
                netdev=f"eth{2 * card + function}",
                pci=f"{prefix}.{function}" if with_pci else None,
            )
    make_numa(root, {0: "0-31", 1: "32-63"})


def per_card_profile(probe, root: Path, host: str = "node-a"):
    node = probe.collect_node(root, with_gpu=False, hostname=host, environ={})
    return probe.build_profile([node], per_card=True)


def test_collect_node_records_the_pci_address_per_device(probe, tmp_path):
    make_nic(tmp_path, "mlx5_0", pci="0000:19:00.0")
    make_nic(tmp_path, "mlx5_1", pci="0000:19:00.1")
    make_nic(tmp_path, "mlx5_2")  # a plain directory, not a symlink
    make_nic(tmp_path, "mlx5_3", numa=None, cpulist=None)  # no device entry at all
    node = probe.collect_node(tmp_path, with_gpu=False, hostname="node-a", environ={})
    assert node["devices"]["mlx5_0"]["pci"] == "0000:19:00.0"
    assert node["devices"]["mlx5_1"]["pci"] == "0000:19:00.1"
    assert node["devices"]["mlx5_2"]["pci"] is None
    assert node["devices"]["mlx5_3"]["pci"] is None


def test_per_card_keeps_the_function_zero_port_of_each_card(probe, tmp_path):
    card_nics(tmp_path)
    profile = per_card_profile(probe, tmp_path)
    assert profile["per_card"] is True
    assert profile["ppn"] == 4
    assert profile["nics"] == ["mlx5_0:1", "mlx5_2:1", "mlx5_4:1", "mlx5_6:1"]
    assert profile["cpus"] == "0,1,32,33"
    assert profile["warnings"] == []
    assert profile["per_nic"]["mlx5_4:1"] == {
        "numa": 1,
        "core": 32,
        "rate": RATE,
        "netdev": "eth4",
        "speed_mbps": 400000,
        "pci": "0000:98:00.0",
    }
    assert [profile["per_nic"][n]["pci"] for n in profile["nics"]] == [
        f"{prefix}.0" for prefix in CARDS
    ]


def test_per_card_falls_back_to_function_one_when_function_zero_is_down(probe, tmp_path):
    card_nics(tmp_path, down=("mlx5_2",))
    profile = per_card_profile(probe, tmp_path)
    assert profile["ppn"] == 4
    assert profile["nics"] == ["mlx5_0:1", "mlx5_3:1", "mlx5_4:1", "mlx5_6:1"]
    assert profile["per_nic"]["mlx5_3:1"]["pci"] == "0000:3b:00.1"
    assert profile["cpus"] == "0,1,32,33"


def test_per_card_leaves_out_a_card_without_an_active_port_and_warns(probe, tmp_path):
    card_nics(tmp_path, dead_cards=(1,))
    profile = per_card_profile(probe, tmp_path)
    assert profile["ppn"] == 3
    assert profile["nics"] == ["mlx5_0:1", "mlx5_4:1", "mlx5_6:1"]
    assert profile["cpus"] == "0,32,33"
    assert any("0000:3b:00" in w and "left out" in w for w in profile["warnings"])


def test_default_merge_still_keeps_every_active_port(probe, tmp_path):
    card_nics(tmp_path)
    node = probe.collect_node(tmp_path, with_gpu=False, hostname="node-a", environ={})
    profile = probe.build_profile([node])
    assert profile["per_card"] is False
    assert profile["ppn"] == 8
    assert profile["nics"] == [f"mlx5_{i}:1" for i in range(8)]
    assert profile["cpus"] == "0,1,2,3,32,33,34,35"
    assert profile["warnings"] == []


def test_per_card_without_pci_addresses_is_an_error_not_a_guess(probe, tmp_path, capsys):
    card_nics(tmp_path, with_pci=False)
    node_file = tmp_path / "probe.jsonl"
    node_file.write_text(_node_line(probe, tmp_path, "node-a", 0) + "\n")
    nodes, _ = probe.parse_node_lines(node_file.read_text().splitlines())
    with pytest.raises(ValueError, match="--per-card needs PCI addresses"):
        probe.build_profile(nodes, per_card=True)
    assert probe.main(["--merge", str(node_file), "--per-card"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("probe: --per-card needs PCI addresses")
    assert captured.out == ""


def test_per_card_flag_needs_merge(probe, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        probe.main(["--per-card", "--sysfs", str(tmp_path), "--no-gpu"])
    assert exc.value.code == 2
    assert "--per-card" in capsys.readouterr().err


def test_per_card_merge_end_to_end_prints_the_pci_column(probe, tmp_path, capsys):
    card_nics(tmp_path / "sys")
    node_file = tmp_path / "probe.jsonl"
    node_file.write_text(_node_line(probe, tmp_path / "sys", "node-a", 0) + "\n")
    assert probe.main(["--merge", str(node_file), "--per-card"]) == 0
    captured = capsys.readouterr()
    profile = json.loads(captured.out)
    assert profile["per_card"] is True and profile["ppn"] == 4
    assert "PCI" in captured.err and "0000:98:00.0" in captured.err


def test_per_card_merge_compares_nodes_card_by_card(probe, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    card_nics(a)
    card_nics(b)
    card_nics(c)
    shutil.rmtree(c / "class" / "infiniband" / "mlx5_6")
    shutil.rmtree(c / "class" / "infiniband" / "mlx5_7")
    lines = [_node_line(probe, a, "node-a", 0), _node_line(probe, b, "node-b", 1)]
    nodes, _ = probe.parse_node_lines(lines)
    profile = probe.build_profile(nodes, per_card=True)
    assert profile["ppn"] == 4 and profile["warnings"] == []

    lines.append(_node_line(probe, c, "node-c", 2))
    nodes, _ = probe.parse_node_lines(lines)
    profile = probe.build_profile(nodes, per_card=True)
    joined = "\n".join(profile["warnings"])
    assert "node-c" in joined and "mlx5_6:1" in joined
    assert "node-b" not in joined
