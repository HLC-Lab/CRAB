"""examples/nplb/topology.sh and topology_summary.py, run against a fake ssh, a fake sysfs tree per
host and fake tcpdump/ip tools in tmp_path. Nothing here runs a real ssh, sinfo, tcpdump or ip, or
touches the network. Host and switch names are made up.

Every host gets `hosts/<name>/` with its own sysfs (`sys/`), its PCI devices (`pci/`), its fake
tools (`bin/`) and the LLDP frames its fake tcpdump prints (`lldp/<netdev>`). The fake ssh runs
the remote command locally with that host's PATH; the head's SYSFS holds a placeholder that the
fake ssh turns into the host's own sysfs root."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SCRIPT = NPLB / "topology.sh"
SUMMARY = NPLB / "topology_summary.py"
PH = "HOSTNAME_PH"
HEADER_FIELDS = 17

FAKE_SSH = rf"""#!/usr/bin/env bash
opts=
while [[ ${{1:-}} == -o ]]; do opts="$opts $2"; shift 2; done
host=$1; shift
echo "$host$opts" >>"$FAKE_SSH_LOG"
dir=$FAKE_HOSTS_DIR/$host
if [[ -f $dir/unreachable ]]; then
    echo "ssh: connect to host $host port 22: No route to host" >&2
    exit 255
fi
cmd="$*"
cmd=${{cmd//{PH}/$host}}
export FAKE_HOST_DIR=$dir
source "$dir/env"
exec bash -c "$cmd"
"""
FAKE_TCPDUMP = r"""#!/usr/bin/env bash
nd=
while (($#)); do
    case $1 in
        -i) nd=$2; shift ;;
    esac
    shift
done
[[ -f $FAKE_HOST_DIR/lldp/$nd ]] && cat "$FAKE_HOST_DIR/lldp/$nd"
exit 0
"""
FAKE_IP = r"""#!/usr/bin/env bash
# ip -4 -o addr show dev <nd>
[[ $1 == -4 && $2 == -o && $3 == addr && $4 == show && $5 == dev ]] || exit 1
[[ -f $FAKE_HOST_DIR/ip/$6 ]] && cat "$FAKE_HOST_DIR/ip/$6"
exit 0
"""
FAKE_SINFO = (
    '#!/usr/bin/env bash\necho "$*" >>"$FAKE_SSH_LOG.sinfo"\nprintf "hostB\\nhostA\\nhostB\\n"\n'
)
REAL_TOOLS = (
    "bash sh env cat sed awk sort uname rm mkdir mktemp dirname tail head grep tr cut readlink "
    "timeout basename date wc"
).split()


def lldp_frame(sysname: str, port: str) -> str:
    return (
        "12:00:00.000000 LLDP, length 250\n"
        "\tChassis ID TLV (1), length 7\n"
        "\t  Subtype MAC address (4): aa:bb:cc:dd:ee:01\n"
        "\tPort ID TLV (2), length 11\n"
        f"\t  Subtype Interface Name (5): {port}\n"
        "\tTime to Live TLV (3), length 2: TTL 120s\n"
        f"\tPort Description TLV (4), length 10: {port}\n"
        f"\tSystem Name TLV (5), length 14: {sysname}\n"
    )


def put(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


def real_tool_dir(root: Path) -> str:
    tools = root / "realbin"
    tools.mkdir()
    for name in REAL_TOOLS:
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    return str(tools)


def build_host(
    root: Path,
    name: str,
    base_path: str,
    *,
    devs: int = 8,
    leaves: tuple[str, str] | None = None,
    speed_mbps: int = 400000,
    down: tuple[int, ...] = (),
    bonds: dict[int, str] | None = None,
    tcpdump: bool = True,
    only_mlx5_0_lldp: bool = False,
) -> Path:
    """A host with `devs` mlx5 devices, two functions per card; function f hears leaves[f]."""
    d = root / "hosts" / name
    sysfs = d / "sys"
    for i in range(devs):
        card, func = divmod(i, 2)
        pci = d / "pci" / f"0000:{0x1A + 0x10 * card:02x}:00.{func}"
        nd = f"eth{i}"
        (pci / "net" / nd).mkdir(parents=True)
        put(pci / "numa_node", f"{card // 2}\n")
        ib = sysfs / "class" / "infiniband" / f"mlx5_{i}"
        (ib / "ports" / "1").mkdir(parents=True)
        (ib / "device").symlink_to(pci)
        is_down = i in down
        put(ib / "ports" / "1" / "state", "1: DOWN\n" if is_down else "4: ACTIVE\n")
        put(ib / "ports" / "1" / "rate", f"{speed_mbps // 1000} Gb/sec (4X NDR)\n")
        put(ib / "ports" / "1" / "link_layer", "Ethernet\n")
        for idx, (gtype, gid) in enumerate(
            [
                ("IB/RoCE v1", "fe80:0000:0000:0000:0000:0000:0000:0001"),
                ("RoCE v2", "0000:0000:0000:0000:0000:0000:0000:0000"),
                ("RoCE v2", f"0000:0000:0000:0000:0000:ffff:0a00:{i:04x}"),
            ]
        ):
            put(ib / "ports" / "1" / "gid_attrs" / "types" / str(idx), gtype + "\n")
            put(ib / "ports" / "1" / "gids" / str(idx), gid + "\n")
        net = sysfs / "class" / "net" / nd
        put(net / "operstate", "down\n" if is_down else "up\n")
        put(net / "speed", "-1\n" if is_down else f"{speed_mbps}\n")
        put(net / "address", f"aa:bb:cc:00:{i:02x}:01\n")
        if bonds and i in bonds:
            (sysfs / "class" / "net" / bonds[i]).mkdir(parents=True, exist_ok=True)
            (net / "master").symlink_to(sysfs / "class" / "net" / bonds[i])
        put(
            d / "ip" / nd,
            f"{i + 2}: {nd}    inet 10.1.{card}.{func + 10}/24 brd 10.1.{card}.255 scope global {nd}\n",
        )
        if leaves is not None and not is_down and (not only_mlx5_0_lldp or i == 0):
            leaf = leaves[func]
            put(d / "lldp" / nd, lldp_frame(f"NV_Leaf{leaf}_CE9866_x", f"400GE1/1/{i + 1}"))
    put(d / "bin" / "ip", FAKE_IP, 0o755)
    if tcpdump:
        put(d / "bin" / "tcpdump", FAKE_TCPDUMP, 0o755)
    put(d / "env", f'export PATH="{d}/bin:{base_path}"\n')
    return d


@pytest.fixture
def world(tmp_path: Path) -> dict:
    base = real_tool_dir(tmp_path)
    gbin = tmp_path / "gbin"
    put(gbin / "ssh", FAKE_SSH, 0o755)
    put(gbin / "sinfo", FAKE_SINFO, 0o755)
    (tmp_path / "work").mkdir()
    return {"root": tmp_path, "gbin": gbin, "base": base, "work": tmp_path / "work"}


def run_topology(
    world: dict, args: list[str], *, drop_path: tuple[str, ...] = (), **env: str
) -> subprocess.CompletedProcess:
    root: Path = world["root"]
    full_env = {
        **os.environ,
        "PATH": f"{world['gbin']}:{os.environ['PATH']}",
        "FAKE_HOSTS_DIR": str(root / "hosts"),
        "FAKE_SSH_LOG": str(root / "ssh.log"),
        "TOPO_SSH": str(world["gbin"] / "ssh"),
        "SYSFS": f"{root}/hosts/{PH}/sys",
        "WORK": str(world["work"]),
        "LLDP_WAIT": "5",
        "PYTHON": sys.executable,
    }
    for var in ("PARTITION", "TOPO_SYSFS"):
        full_env.pop(var, None)
    full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=120
    )


def tsv_files(world: dict) -> list[Path]:
    return sorted(world["work"].glob("topology_*.tsv"))


def summary_lines(result: subprocess.CompletedProcess) -> list[str]:
    return result.stdout.splitlines()


def find(lines: list[str], *needles: str) -> str:
    for line in lines:
        if all(n in line for n in needles):
            return line
    raise AssertionError(f"no line with {needles} in:\n" + "\n".join(lines))


def two_pod_world(world: dict, names: list[str], leaf_pairs: list[tuple[str, str]], **kw) -> None:
    for name, leaves in zip(names, leaf_pairs, strict=True):
        build_host(world["root"], name, world["base"], leaves=leaves, **kw)


def test_scripts_exist_and_the_shell_one_is_executable():
    assert os.access(SCRIPT, os.X_OK)
    assert SUMMARY.is_file()


# 1. two hosts, two pods --------------------------------------------------------------------


def test_two_hosts_in_two_pods(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"))
    build_host(world["root"], "hostB", world["base"], leaves=("3", "4"), speed_mbps=800000)
    result = run_topology(world, ["hostA", "hostB"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    row0 = find(lines, "mlx5_0 ")
    assert "400 L1:1/1/1" in row0 and "800 L3:1/1/1" in row0
    row1 = find(lines, "mlx5_1 ")
    assert "400 L2:1/1/2" in row1 and "800 L4:1/1/2" in row1
    head = find(lines, "n1:hostA")
    assert "n2:hostB" in head
    assert find(lines, "hostA", "pod A")
    assert find(lines, "hostB", "pod B")
    assert "hostA-hostB cross-pod" in find(lines, "pairs")
    assert "hostA f0→L1 f1→L2 on 4/4" in find(lines, "cards")
    assert "hostB f0→L3 f1→L4 on 4/4" in find(lines, "cards")
    assert find(lines, "pods:", "A = L1/L2", "B = L3/L4", "lab figure")
    assert "LLDP heard on 16/16 active ports" in find(lines, "LLDP heard")
    verdict = lines.index("== VERDICT")
    assert verdict > lines.index(row0)
    assert lines[-1] != "" and len(lines) <= 40


def test_ssh_is_called_in_batch_mode_once_per_host_and_hosts_come_from_sinfo(world):
    for name in ("hostA", "hostB"):
        build_host(world["root"], name, world["base"], leaves=("1", "2"))
    result = run_topology(world, [], PARTITION="somepart")
    assert result.returncode == 0, result.stdout + result.stderr
    log = (world["root"] / "ssh.log").read_text().splitlines()
    assert sorted(log) == sorted(
        [
            "hostA BatchMode=yes ConnectTimeout=5",
            "hostB BatchMode=yes ConnectTimeout=5",
        ]
    )
    sinfo_args = (world["root"] / "ssh.log.sinfo").read_text()
    assert "somepart" in sinfo_args


def test_no_hosts_and_no_sinfo_is_a_setup_error(world):
    (world["gbin"] / "sinfo").unlink()
    # keep a PATH without any real sinfo
    path = f"{world['gbin']}:{world['base']}"
    result = run_topology(world, [], PATH=path)
    assert result.returncode == 2
    assert "sinfo" in result.stderr


# 2. the victim pair --------------------------------------------------------------------------


def test_victim_pair_crosses_the_spine_with_pods_aabb(world):
    two_pod_world(
        world,
        ["hostA", "hostB", "hostC", "hostD"],
        [("1", "2"), ("1", "2"), ("3", "4"), ("3", "4")],
    )
    result = run_topology(world, ["hostA", "hostB", "hostC", "hostD"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert "victim pair (n1+n3) crosses the spine: YES" in lines
    pairs = find(lines, "pairs")
    assert "hostA-hostB same-pod" in pairs
    assert "hostA-hostC cross-pod" in pairs
    assert "hostA-hostD cross-pod" in pairs


def test_victim_pair_does_not_cross_with_pods_abab(world):
    two_pod_world(
        world,
        ["hostA", "hostB", "hostC", "hostD"],
        [("1", "2"), ("3", "4"), ("1", "2"), ("3", "4")],
    )
    result = run_topology(world, ["hostA", "hostB", "hostC", "hostD"])
    assert "victim pair (n1+n3) crosses the spine: NO" in summary_lines(result)


def test_a_host_without_lldp_has_pod_unknown_and_the_victim_verdict_is_unknown(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"))
    build_host(world["root"], "hostB", world["base"], leaves=("1", "2"))
    build_host(world["root"], "hostC", world["base"], leaves=None)
    build_host(world["root"], "hostD", world["base"], leaves=("3", "4"))
    result = run_topology(world, ["hostA", "hostB", "hostC", "hostD"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert "pod ?" in find(lines, "hostC", "pod")
    assert "victim pair (n1+n3) crosses the spine: UNKNOWN" in lines
    assert "hostA-hostC unknown" in find(lines, "pairs")


# 3. down ports, bonds, ports per node ----------------------------------------------------------


def test_down_port_bond_marker_and_ports_per_node(world):
    build_host(
        world["root"],
        "hostA",
        world["base"],
        leaves=("1", "2"),
        down=(6, 7),
        bonds={2: "bond0", 3: "bond0"},
    )
    result = run_topology(world, ["hostA"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert find(lines, "mlx5_6 ").split()[1:] == ["down"]
    cell = find(lines, "mlx5_2 ")
    assert cell.split() == ["mlx5_2", "400", "L1:1/1/3", "b"]
    assert "hostA: 6 active, 6x400G, 2 down" in lines
    bonds = find(lines, "bonds")
    assert "hostA" in bonds and "bond0" in bonds and "mlx5_2" in bonds and "mlx5_3" in bonds
    assert "LLDP heard on 6/6 active ports" in lines


def test_ports_per_node_line_with_every_port_active(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"))
    lines = summary_lines(run_topology(world, ["hostA"]))
    assert "hostA: 8 active, 8x400G" in lines
    assert "bonds: none" in lines


# 4. no LLDP, no tcpdump ---------------------------------------------------------------------


def test_no_lldp_anywhere_points_at_the_switch_reader(world):
    for name in ("hostA", "hostB"):
        build_host(world["root"], name, world["base"], leaves=None)
    result = run_topology(world, ["hostA", "hostB"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert "LLDP heard on 0/16 active ports" in lines
    assert "LLDP not visible on hosts: use the switch reader" in lines
    assert "400 -" in find(lines, "mlx5_0 ")
    assert "pod ?" in find(lines, "hostA", "pod")


def test_missing_tcpdump_warns_and_the_rest_is_printed(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"), tcpdump=False)
    result = run_topology(world, ["hostA"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert "WARN hostA: tcpdump not found" in find(lines, "WARN")
    assert "400 -" in find(lines, "mlx5_0 ")
    assert "hostA: 8 active, 8x400G" in lines
    assert "LLDP not visible on hosts: use the switch reader" in lines


# 5. an unreachable host ------------------------------------------------------------------------


def test_unreachable_host_is_a_fail_line_and_exit_1(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"))
    build_host(world["root"], "hostB", world["base"], leaves=("3", "4"))
    (world["root"] / "hosts" / "hostB" / "unreachable").write_text("")
    result = run_topology(world, ["hostA", "hostB"])
    assert result.returncode == 1, result.stdout + result.stderr
    lines = summary_lines(result)
    assert "FAIL hostB: ssh exited 255" in find(lines, "FAIL")
    assert "unreachable" in find(lines, "mlx5_0 ")
    assert "400 L1:1/1/1" in find(lines, "mlx5_0 ")
    assert find(lines, "hostA", "pod A")
    assert find(lines, "hostB:", "unreachable")


# 6. size limits -------------------------------------------------------------------------------


def test_summary_fits_one_screen_for_four_hosts_with_18_devices(world):
    for name, leaves in zip(
        ("hostA", "hostB", "hostC", "hostD"),
        (("1", "2"), ("1", "2"), ("3", "4"), ("3", "4")),
        strict=True,
    ):
        build_host(
            world["root"],
            name,
            world["base"],
            devs=18,
            leaves=leaves,
            down=(6, 7),
            speed_mbps=800000,
        )
    result = run_topology(world, ["hostA", "hostB", "hostC", "hostD"])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert len(lines) <= 40, "\n".join(lines)
    assert max(len(ln) for ln in lines) <= 120
    grid = [ln.split()[0] for ln in lines if ln.startswith("mlx5_")]
    assert len(grid) == 18
    assert grid.index("mlx5_2") < grid.index("mlx5_10")
    assert lines[-1] != ""


def test_a_fifth_host_only_adds_a_note_and_the_summary_still_fits(world):
    names = ["hostA", "hostB", "hostC", "hostD", "hostE", "hostF"]
    for name in names:
        build_host(world["root"], name, world["base"], devs=18, leaves=("1", "2"))
    result = run_topology(world, names)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary_lines(result)
    assert len(lines) <= 40, "\n".join(lines)
    assert max(len(ln) for ln in lines) <= 120
    assert "n5:" not in find(lines, "n1:hostA")
    assert find(lines, "6 hosts", "first 4")


# 7. the TSV log -------------------------------------------------------------------------------


def test_tsv_log_has_a_header_and_one_line_per_host_and_port(world):
    build_host(world["root"], "hostA", world["base"], leaves=("1", "2"))
    build_host(world["root"], "hostB", world["base"], leaves=("3", "4"), bonds={0: "bond0"})
    result = run_topology(world, ["hostA", "hostB"])
    assert result.returncode == 0, result.stdout + result.stderr
    files = tsv_files(world)
    assert len(files) == 1
    assert re.fullmatch(r"topology_\d{8}-\d{6}\.tsv", files[0].name)
    lines = files[0].read_text().splitlines()
    assert len(lines) == 1 + 16
    header = lines[0].split("\t")
    assert header[:3] == ["host", "dev", "port"] and len(header) == HEADER_FIELDS
    rows = [ln.split("\t") for ln in lines[1:]]
    assert all(len(r) == HEADER_FIELDS for r in rows)
    by_key = {(r[0], r[1]): dict(zip(header, r, strict=True)) for r in rows}
    a0 = by_key[("hostA", "mlx5_0")]
    assert a0["port"] == "1"
    assert a0["state"] == "4: ACTIVE"
    assert a0["rate"] == "400 Gb/sec (4X NDR)"
    assert a0["link_layer"] == "Ethernet"
    assert a0["netdev"] == "eth0"
    assert a0["operstate"] == "up"
    assert a0["speed_mbps"] == "400000"
    assert a0["mac"] == "aa:bb:cc:00:00:01"
    assert a0["bond"] == "-"
    assert a0["ipv4"] == "10.1.0.10/24"
    assert a0["gid"] == "2"
    assert a0["pci"] == "0000:1a:00.0"
    assert a0["numa"] == "0"
    assert a0["lldp_sys"] == "NV_Leaf1_CE9866_x"
    assert a0["lldp_port"] == "400GE1/1/1"
    assert by_key[("hostB", "mlx5_0")]["bond"] == "bond0"
    assert by_key[("hostB", "mlx5_7")]["lldp_sys"] == "NV_Leaf4_CE9866_x"
    assert str(files[0]) in result.stdout


def test_work_defaults_to_local_nplb_work_in_the_checkout():
    text = SCRIPT.read_text()
    assert "local/nplb-work" in text
    assert "WORK=${WORK:-" in text


def test_lldp_falls_back_to_local_subtype_and_then_to_port_description(world):
    build_host(world["root"], "hostA", world["base"], devs=3, leaves=("1", "2"))
    lldp = world["root"] / "hosts" / "hostA" / "lldp"
    put(
        lldp / "eth1",
        lldp_frame("NV_Leaf2_CE9866_x", "x").replace(
            "Subtype Interface Name (5): x", "Subtype Local (7): 400GE1/1/9"
        ),
    )
    put(
        lldp / "eth2",
        lldp_frame("NV_Leaf1_CE9866_x", "x")
        .replace(
            "\t  Subtype Interface Name (5): x\n",
            "\t  Subtype MAC address (4): aa:bb:cc:dd:ee:02\n",
        )
        .replace(
            "Port Description TLV (4), length 10: x",
            "Port Description TLV (4), length 10: 400GE1/1/7",
        ),
    )
    result = run_topology(world, ["hostA"])
    assert result.returncode == 0, result.stdout + result.stderr
    rows = {
        r.split("\t")[1]: r.split("\t") for r in tsv_files(world)[0].read_text().splitlines()[1:]
    }
    assert rows["mlx5_1"][16] == "400GE1/1/9"
    assert rows["mlx5_2"][16] == "400GE1/1/7"


# 8. read-only ----------------------------------------------------------------------------------


def test_script_text_contains_no_write_command():
    for path in (SCRIPT, SUMMARY):
        text = path.read_text()
        for needle in ("mlxreg", " -set", "ip link set", "ethtool -s", "sysctl -w"):
            assert needle not in text, f"{path.name}: {needle}"
        assert not re.search(r"echo[^\n]*>\s*\"?\$?\{?(sysfs|SYSFS|/sys)", text), path.name


# 9. the renderer on TSV text ------------------------------------------------------------------


@pytest.fixture(scope="module")
def summary() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nplb_topology_summary", SUMMARY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["nplb_topology_summary"] = module
    spec.loader.exec_module(module)
    return module


TSV_HEADER = "\t".join(
    "host dev port state rate link_layer netdev operstate speed_mbps mac bond ipv4 gid pci numa "
    "lldp_sys lldp_port".split()
)


def tsv_row(
    host, dev, *, state="4: ACTIVE", sysname="-", port="-", pci="-", bond="-", speed="400000"
):
    return "\t".join(
        [host, dev, "1", state, "400 Gb/sec (4X NDR)", "Ethernet", "eth0", "up", speed,
         "aa:bb", bond, "-", "-", pci, "0", sysname, port]
    )  # fmt: skip


def test_leaf_label(summary):
    assert summary.leaf_label("NV_Leaf3_CE9866_x") == "L3"
    assert summary.leaf_label("Spine2-core") == "S2"
    assert summary.leaf_label("weirdname_long") == "weirdn"
    assert summary.leaf_label("-") == "-"


def test_port_label(summary):
    assert summary.port_label("400GE1/1/5") == "1/1/5"
    assert summary.port_label("Ethernet1/2") == "1/2"
    assert summary.port_label("swp17_long_name") == "ong_name"
    assert summary.port_label("-") == "-"


def test_pod_rule(summary):
    assert summary.pod_of({"L1", "L2"}) == "A"
    assert summary.pod_of({"L3", "L4"}) == "B"
    assert summary.pod_of({"L1", "L3"}) == "?"
    assert summary.pod_of({"L1", "S1"}) == "?"
    assert summary.pod_of(set()) == "?"


def test_render_orders_devices_naturally_and_hosts_by_first_appearance(summary):
    text = "\n".join(
        [
            TSV_HEADER,
            tsv_row("hostZ", "mlx5_10", sysname="NV_Leaf1_x", port="400GE1/1/5"),
            tsv_row("hostZ", "mlx5_2", sysname="NV_Leaf2_x", port="400GE1/1/6"),
            tsv_row("hostA", "mlx5_2", state="1: DOWN"),
        ]
    )
    lines = summary.render(text, "topology_20261015-120000.tsv")
    grid = [ln.split()[0] for ln in lines if ln.startswith("mlx5_")]
    assert grid == ["mlx5_2", "mlx5_10"]
    head = find(lines, "n1:hostZ")
    assert head.index("n1:hostZ") < head.index("n2:hostA")
    assert "down" in find(lines, "mlx5_2 ")
    assert find(lines, "2026-10-15 12:00:00")
    assert find(lines, "log: topology_20261015-120000.tsv")


def test_render_names_a_pod_from_the_leaves_heard_and_counts_cards_heard_on_both(summary):
    text = "\n".join(
        [
            TSV_HEADER,
            tsv_row("hostA", "mlx5_0", sysname="NV_Leaf1_x", port="400GE1/1/5", pci="0000:1a:00.0"),
            tsv_row("hostA", "mlx5_1", pci="0000:1a:00.1"),
        ]
    )
    lines = summary.render(text, "x.tsv")
    assert "pod A (L1)" in find(lines, "hostA", "pod")
    assert "hostA f0/f1 both heard on 0/1" in find(lines, "cards")
