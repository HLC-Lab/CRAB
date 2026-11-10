"""examples/nplb/topo_map.py and topo_tools.sh (`nplb topo`), run against synthetic probe TSVs.

The TSVs are written here in the format of topology.sh (the FIELDS of topology_summary.py); host and
switch names are made up and no real LLDP output was ever seen, so the switch names follow the shape
the summary parses (`NV_Leaf<N>_<model>_<x>`). The probe itself is a fake topology.sh (env
TOPOLOGY_SH) that writes a canned TSV into $WORK and prints junk. Nothing here runs ssh or touches
the network.

The slot tables come from NodeAllocator.allocate_partitioned (src/crab/core/allocation/allocator.py,
the partitions block of examples/nplb/h100/gpu_a2a_comm.json: victim and aggressor share 50 each,
one app per partition): with 4 nodes the counts are [2, 2]; top-level mode `interleaved` (stride 1)
gives the victim node-list slots 0 and 2 and the aggressor 1 and 3, mode `linear` gives the victim
slots 0 and 1 and the aggressor 2 and 3."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
MAP = NPLB / "topo_map.py"
TOOLS = NPLB / "topo_tools.sh"
BASH = shutil.which("bash") or "/bin/bash"
NOW = "2026-11-10T09:30:00"
FIELDS = (
    "host dev port state rate link_layer netdev operstate speed_mbps mac bond ipv4 gid pci numa "
    "lldp_sys lldp_port"
).split()
HOSTS4 = ["nodeA", "nodeB", "nodeC", "nodeD"]


def sysname(leaf: int) -> str:
    return f"NV_Leaf{leaf}_CE9866_x"


def port_row(host: str, dev: int, leaf: int | None, state: str = "4: ACTIVE", sys: str = "") -> str:
    """One TSV line in the FIELDS order. LEAF None prints no LLDP (the probe writes "-")."""
    lldp = sys or (sysname(leaf) if leaf is not None else "-")
    values = {
        "host": host,
        "dev": f"mlx5_{dev}",
        "port": "1",
        "state": state,
        "rate": "400 Gb/sec (4X NDR)",
        "link_layer": "Ethernet",
        "netdev": f"eth{dev}",
        "operstate": "up",
        "speed_mbps": "400000",
        "mac": "aa:bb:cc:00:00:0" + str(dev % 10),
        "bond": "-",
        "ipv4": "-",
        "gid": "3",
        "pci": f"0000:0{dev}:00.0",
        "numa": "0",
        "lldp_sys": lldp,
        "lldp_port": "400GE1/1/5" if lldp != "-" else "-",
    }
    return "\t".join(values[f] for f in FIELDS)


def host_rows(host: str, leaves: list[int | None]) -> list[str]:
    """One active port per entry of LEAVES, port i on leaves[i]."""
    return [port_row(host, i, leaf) for i, leaf in enumerate(leaves)]


def write_tsv(path: Path, rows: list[str], records: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(FIELDS), *rows, *(records or [])]
    path.write_text("\n".join(lines) + "\n")
    return path


def two_pods(path: Path) -> Path:
    """nodeA, nodeB on leaves 1 and 2 (both ports each); nodeC, nodeD on leaves 3 and 4."""
    rows: list[str] = []
    for host in HOSTS4[:2]:
        rows += host_rows(host, [1, 2, 1, 2])
    for host in HOSTS4[2:]:
        rows += host_rows(host, [3, 4, 3, 4])
    return write_tsv(path, rows)


def make_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def clean_env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NODE_LEAF", "WORK", "PYTHONPATH")}
    env["NPLB_WORK"] = str(tmp / "work")
    env["NPLB_REPORT_DIR"] = str(tmp / "reports")
    env["NPLB_NOW"] = NOW
    env.update(extra)
    return env


def run_map(tmp: Path, tsv: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MAP), "build", "--tsv", str(tsv), *args],
        env=clean_env(tmp, **env),
        capture_output=True,
        text=True,
        timeout=60,
    )


def load_map(tmp: Path) -> dict:
    return json.loads((tmp / "work" / "node_map.json").read_text())


def verdict_of(stdout: str) -> str:
    return stdout.splitlines()[0]


def test_fields_match_the_probe_summary():
    spec = importlib.util.spec_from_file_location("topology_summary", NPLB / "topology_summary.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["topology_summary"] = module
    spec.loader.exec_module(module)
    assert FIELDS == list(module.FIELDS)


def test_two_pods_cross_and_same(tmp_path):
    result = run_map(tmp_path, two_pods(tmp_path / "t.tsv"))
    assert result.returncode == 0, result.stderr
    assert verdict_of(result.stdout) == "VERDICT: OK leaves known for 4 of 4 nodes"
    node_map = load_map(tmp_path)
    assert node_map["hosts"] == HOSTS4
    assert node_map["leaf"] == {
        "nodeA": ["L1", "L2"],
        "nodeB": ["L1", "L2"],
        "nodeC": ["L3", "L4"],
        "nodeD": ["L3", "L4"],
    }
    split_i = node_map["layouts"]["split-i"]
    assert split_i["victim"] == ["nodeA", "nodeC"]
    assert split_i["aggressor"] == ["nodeB", "nodeD"]
    assert split_i["victim_pair"] == "cross"
    assert split_i["aggressor_pair"] == "cross"
    assert split_i["shared_leaves"] == ["L1", "L2", "L3", "L4"]
    split_l = node_map["layouts"]["split-l"]
    assert split_l["victim"] == ["nodeA", "nodeB"]
    assert split_l["aggressor"] == ["nodeC", "nodeD"]
    assert split_l["victim_pair"] == "same"
    assert split_l["aggressor_pair"] == "same"
    assert split_l["shared_leaves"] == []


def test_ports_down_are_ignored_and_majority_is_not_used(tmp_path):
    rows = [
        # ports 0-2 heard leaf 1, port 3 leaf 2: both stay in the set, no majority vote
        *host_rows("nodeA", [1, 1, 1, 2]),
        # a Down port that heard leaf 9 does not count, nor does an active port without LLDP
        port_row("nodeB", 0, 5),
        port_row("nodeB", 1, 9, state="1: DOWN"),
        port_row("nodeB", 2, None),
        # whitespace around the switch name is stripped
        port_row("nodeC", 0, None, sys="  NV_Leaf7_CE9866_x  "),
    ]
    result = run_map(tmp_path, write_tsv(tmp_path / "t.tsv", rows), "--hosts", "nodeA,nodeB,nodeC")
    assert result.returncode == 0, result.stderr
    leaf = load_map(tmp_path)["leaf"]
    assert leaf == {"nodeA": ["L1", "L2"], "nodeB": ["L5"], "nodeC": ["L7"]}
    table = {
        ln.split()[0]: ln.split() for ln in result.stdout.splitlines() if ln.startswith("node")
    }
    assert table["nodeA"][1] == "L1+L2"
    assert table["nodeB"][2] == "2/3"  # two of its three ports are up (one of them heard nothing)


def test_missing_lldp_for_one_node_is_a_warning(tmp_path):
    rows = []
    for host in HOSTS4[:3]:
        rows += host_rows(host, [1, 2])
    rows += host_rows("nodeD", [None, None])
    result = run_map(tmp_path, write_tsv(tmp_path / "t.tsv", rows))
    assert result.returncode == 0, result.stderr
    assert verdict_of(result.stdout) == "VERDICT: WARN leaf unknown for nodeD (no LLDP heard)"
    node_map = load_map(tmp_path)
    assert node_map["source"] == "lldp+slurm-order"
    assert node_map["leaf"]["nodeD"] is None
    assert node_map["layouts"]["split-l"]["aggressor_pair"] == "unknown"
    assert node_map["layouts"]["split-i"]["aggressor_pair"] == "unknown"
    assert node_map["layouts"]["split-l"]["victim_pair"] == "same"


def test_no_lldp_at_all_uses_manual_override(tmp_path):
    rows = [row for host in HOSTS4 for row in host_rows(host, [None, None])]
    tsv = write_tsv(tmp_path / "t.tsv", rows)
    env = "nodeA=L1,nodeB=L1,nodeC=L2,nodeD=L2"
    result = run_map(tmp_path, tsv, NODE_LEAF=env)
    assert result.returncode == 0, result.stderr
    assert verdict_of(result.stdout) == "VERDICT: OK leaves from manual override (NODE_LEAF)"
    node_map = load_map(tmp_path)
    assert node_map["source"] == "manual"
    assert node_map["leaf"] == {"nodeA": ["L1"], "nodeB": ["L1"], "nodeC": ["L2"], "nodeD": ["L2"]}
    assert node_map["layouts"]["split-l"]["victim_pair"] == "same"
    assert node_map["layouts"]["split-i"]["victim_pair"] == "cross"

    # the same map from a file, with comments; the env var wins over the file per host
    work = tmp_path / "work"
    (work / "node_leaf.txt").write_text(
        "# host leaf\nnodeA L1\nnodeB L1  # same leaf as A\n\nnodeC L2\nnodeD L2\n"
    )
    from_file = run_map(tmp_path, tsv)
    assert from_file.returncode == 0, from_file.stderr
    assert verdict_of(from_file.stdout).startswith("VERDICT: OK leaves from manual override")
    assert load_map(tmp_path)["leaf"] == node_map["leaf"]
    both = run_map(tmp_path, tsv, NODE_LEAF="nodeD=L9")
    assert load_map(tmp_path)["leaf"]["nodeD"] == ["L9"]
    assert load_map(tmp_path)["leaf"]["nodeC"] == ["L2"]
    assert both.returncode == 0

    # a malformed entry stops with exit 2 and names the entry
    bad = run_map(tmp_path, tsv, NODE_LEAF="nodeA=L1,oops")
    assert bad.returncode == 2
    assert "oops" in bad.stderr
    (work / "node_leaf.txt").write_text("nodeA\n")
    bad_file = run_map(tmp_path, tsv)
    assert bad_file.returncode == 2
    assert "nodeA" in bad_file.stderr


def test_override_beats_lldp_per_host_and_is_reported(tmp_path):
    result = run_map(tmp_path, two_pods(tmp_path / "t.tsv"), NODE_LEAF="nodeB=L3")
    assert result.returncode == 0, result.stderr
    node_map = load_map(tmp_path)
    assert node_map["leaf"]["nodeA"] == ["L1", "L2"]
    assert node_map["leaf"]["nodeB"] == ["L3"]
    assert node_map["source"] == "lldp+manual+slurm-order"
    assert any("overridden" in ln and "nodeB" in ln for ln in result.stdout.splitlines())
    table = {ln.split()[0]: ln.split() for ln in result.stdout.splitlines() if ln[:4] == "node"}
    assert table["nodeB"][-1] == "manual"
    assert table["nodeA"][-1] == "lldp"


def test_no_lldp_and_no_override_is_unknown_but_usable(tmp_path):
    rows = [row for host in HOSTS4 for row in host_rows(host, [None])]
    result = run_map(tmp_path, write_tsv(tmp_path / "t.tsv", rows))
    assert result.returncode == 0, result.stderr
    assert verdict_of(result.stdout) == (
        "VERDICT: WARN leaf unknown for all nodes: set NODE_LEAF=host=leaf,... "
        "or $WORK/node_leaf.txt"
    )
    node_map = load_map(tmp_path)
    assert set(node_map["leaf"].values()) == {None}
    assert node_map["layouts"]["split-i"]["victim"] == ["nodeA", "nodeC"]
    assert node_map["layouts"]["split-i"]["victim_pair"] == "unknown"
    assert node_map["layouts"]["split-l"]["aggressor"] == ["nodeC", "nodeD"]
    assert node_map["layouts"]["split-l"]["aggressor_pair"] == "unknown"
    assert node_map["layouts"]["split-l"]["shared_leaves"] == []
    assert "nodeA" in result.stdout and "split-l" in result.stdout


def test_hosts_order_is_sorted_by_name_and_not_by_probe_order(tmp_path):
    names = ["node10", "node9", "node110", "node11"]
    rows = [row for host in names for row in host_rows(host, [1])]
    result = run_map(
        tmp_path, write_tsv(tmp_path / "t.tsv", rows), "--hosts", "node11,node110,node9,node10"
    )
    assert result.returncode == 0, result.stderr
    node_map = load_map(tmp_path)
    assert node_map["hosts"] == ["node9", "node10", "node11", "node110"]
    assert node_map["layouts"]["split-l"]["victim"] == ["node9", "node10"]
    assert node_map["layouts"]["split-i"]["victim"] == ["node9", "node11"]
    # without --hosts the TSV order is not used either
    again = run_map(tmp_path, tmp_path / "t.tsv")
    assert again.returncode == 0
    assert load_map(tmp_path)["hosts"] == ["node9", "node10", "node11", "node110"]


def test_map_json_shape(tmp_path):
    result = run_map(tmp_path, two_pods(tmp_path / "t.tsv"))
    assert result.returncode == 0, result.stderr
    path = tmp_path / "work" / "node_map.json"
    node_map = json.loads(path.read_text())
    assert set(node_map) >= {"when", "source", "hosts", "leaf", "layouts"}
    assert node_map["when"] == NOW
    assert isinstance(node_map["hosts"], list)
    assert set(node_map["layouts"]) == {"split-i", "split-l", "shared"}
    for layout in node_map["layouts"].values():
        assert set(layout) == {
            "victim",
            "aggressor",
            "victim_pair",
            "aggressor_pair",
            "shared_leaves",
        }
        assert layout["victim_pair"] in ("same", "cross", "partial", "unknown")
        assert layout["aggressor_pair"] in ("same", "cross", "partial", "unknown")
    shared = node_map["layouts"]["shared"]
    assert shared["victim"] == shared["aggressor"] == HOSTS4
    assert shared["victim_pair"] == shared["aggressor_pair"] == "partial"
    assert shared["shared_leaves"] == ["L1", "L2", "L3", "L4"]
    # written through a temp file and renamed: no leftovers, and a rewrite replaces the file
    assert sorted(p.name for p in path.parent.iterdir()) == ["node_map.json"]
    run_map(tmp_path, tmp_path / "t.tsv", NODE_LEAF="nodeA=L9")
    assert load_map(tmp_path)["leaf"]["nodeA"] == ["L9"]
    # --out writes elsewhere
    out = tmp_path / "elsewhere" / "m.json"
    assert run_map(tmp_path, tmp_path / "t.tsv", "--out", str(out)).returncode == 0
    assert json.loads(out.read_text())["hosts"] == HOSTS4


def test_partial_overlap_is_classified_partial(tmp_path):
    rows = [
        *host_rows("nodeA", [1, 2]),
        *host_rows("nodeB", [2, 3]),
        *host_rows("nodeC", [1, 2]),
        *host_rows("nodeD", [4]),
    ]
    assert run_map(tmp_path, write_tsv(tmp_path / "t.tsv", rows)).returncode == 0
    layouts = load_map(tmp_path)["layouts"]
    assert layouts["split-l"]["victim_pair"] == "partial"  # {L1,L2} and {L2,L3}
    assert layouts["split-i"]["victim_pair"] == "same"  # nodeA and nodeC
    assert layouts["split-i"]["aggressor_pair"] == "cross"  # {L2,L3} and {L4}


def test_probe_fail_records_are_tolerated(tmp_path):
    tsv = write_tsv(
        tmp_path / "t.tsv",
        [row for host in HOSTS4[:3] for row in host_rows(host, [1, 2])],
        ["#FAIL\tnodeD\tssh exited 255: No route to host", "#WARN\tnodeA\ttcpdump not found"],
    )
    result = run_map(tmp_path, tsv, "--probe-rc", "1")
    assert result.returncode == 0, result.stderr
    first = verdict_of(result.stdout)
    assert first.startswith("VERDICT: WARN")
    assert "nodeD" in first
    assert "probe exit 1" in first
    assert load_map(tmp_path)["leaf"]["nodeD"] is None


def test_report_fits_pages_and_starts_with_verdict(tmp_path):
    work = tmp_path / "work"
    two_pods(work / "topology_20261110-093000.tsv")
    result = subprocess.run(
        [BASH, str(TOOLS), "topo", "--from", str(work / "topology_20261110-093000.tsv")],
        env=clean_env(tmp_path, WORK=str(work)),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("topo | page 1/")
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
    body = lines[1:-1]
    assert body[0] == "VERDICT: OK leaves known for 4 of 4 nodes"
    assert any(ln.split() == ["host", "leaves", "ports", "up", "source"] for ln in body)
    assert any(ln.split()[:2] == ["layout", "role"] for ln in body)
    assert any(ln.startswith("split-i") for ln in body)
    assert any(ln.startswith("split-l") for ln in body)
    saved = sorted((tmp_path / "reports").glob("*_topo.txt"))
    assert len(saved) == 1
    whole = saved[0].read_text().splitlines()
    assert whole[-1] == f"map saved: {work / 'node_map.json'}"


# ---- topo_tools.sh with a fake probe ----

FAKE_PROBE = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_PROBE_LOG"
echo "JUNK SCREEN LINE"
echo "junk on stderr" >&2
[[ ${FAKE_PROBE_RC:-0} == 2 ]] && { echo "topology: no nodes" >&2; exit 2; }
mkdir -p "$WORK"
cp "$FAKE_TSV" "$WORK/topology_20261110-093000.tsv"
exit "${FAKE_PROBE_RC:-0}"
"""


@pytest.fixture
def site(tmp_path: Path) -> dict:
    probe = tmp_path / "fake" / "topology.sh"
    make_exe(probe, FAKE_PROBE)
    work = tmp_path / "work"
    return {
        "tmp": tmp_path,
        "work": work,
        "probe": probe,
        "log": tmp_path / "probe.log",
        "tsv": two_pods(tmp_path / "canned.tsv"),
    }


def run_tools(site: dict, *args: str, script: Path = TOOLS, **env: str):
    log: Path = site["log"]
    log.touch()
    full = clean_env(
        site["tmp"],
        WORK=str(site["work"]),
        TOPOLOGY_SH=str(site["probe"]),
        FAKE_PROBE_LOG=str(log),
        FAKE_TSV=str(site["tsv"]),
        LLDP_WAIT="7",
        **env,
    )
    return subprocess.run(
        [BASH, str(script), *args],
        env=full,
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_topo_tools_runs_probe_then_map_and_narrates(site):
    result = run_tools(site, "topo", "nodeB", "nodeA")
    assert result.returncode == 0, result.stdout + result.stderr
    assert site["log"].read_text().splitlines() == ["nodeB nodeA"]
    assert "JUNK SCREEN LINE" in (site["work"] / "topo_probe.log").read_text()
    assert "JUNK" not in result.stdout and "JUNK" not in result.stderr
    notes = [ln for ln in result.stderr.splitlines() if ln.startswith("[")]
    assert len(notes) >= 2
    for note in notes:
        assert note[0] == "[" and note[3] == ":" and note[6] == ":" and note[9] == "]"
    assert any("waiting up to 7s for LLDP" in n for n in notes)
    assert any("node map" in n for n in notes)
    lines = result.stdout.splitlines()
    assert lines[0].startswith("topo | page 1/")
    assert lines[1].startswith("VERDICT: ")
    # only the hosts named on the command line are mapped
    assert load_map(site["tmp"])["hosts"] == ["nodeA", "nodeB"]


def test_topo_tools_default_wait_hint(site):
    env = clean_env(site["tmp"], WORK=str(site["work"]), TOPOLOGY_SH=str(site["probe"]))
    env.update(FAKE_PROBE_LOG=str(site["log"]), FAKE_TSV=str(site["tsv"]))
    env.pop("LLDP_WAIT", None)
    result = subprocess.run(
        [BASH, str(TOOLS), "topo"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "waiting up to 40s for LLDP" in result.stderr


def test_probe_exit_1_still_builds_the_map(site):
    rows = [row for host in HOSTS4[:3] for row in host_rows(host, [1, 2])]
    site["tsv"] = write_tsv(
        site["tmp"] / "canned.tsv", rows, ["#FAIL\tnodeD\tssh exited 255: No route to host"]
    )
    result = run_tools(site, "topo", FAKE_PROBE_RC="1")
    assert result.returncode == 0, result.stdout + result.stderr
    first = result.stdout.splitlines()[1]
    assert first.startswith("VERDICT: WARN") and "probe exit 1" in first
    assert (site["work"] / "node_map.json").is_file()


def test_probe_exit_1_with_nothing_heard_exits_1(site):
    site["tsv"] = write_tsv(
        site["tmp"] / "canned.tsv",
        [],
        [f"#FAIL\t{h}\tssh exited 255: No route to host" for h in HOSTS4],
    )
    result = run_tools(site, "topo", FAKE_PROBE_RC="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "probe exit 1" in result.stdout


def test_probe_exit_2_stops_without_a_report(site):
    result = run_tools(site, "topo", FAKE_PROBE_RC="2")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "topology: no nodes" in result.stderr
    assert not (site["work"] / "node_map.json").exists()
    assert not (site["tmp"] / "reports").exists()


def test_from_tsv_does_not_probe(site):
    assert not site["work"].exists()
    result = run_tools(site, "topo", "--from", str(site["tsv"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert site["log"].read_text() == ""
    assert result.stdout.splitlines()[1].startswith("VERDICT: OK")
    assert (site["work"] / "node_map.json").is_file()
    missing = run_tools(site, "topo", "--from", str(site["tmp"] / "nope.tsv"))
    assert missing.returncode == 2
    assert "nope.tsv" in missing.stderr
    assert missing.stdout == ""
    assert run_tools(site, "topo", "--from").returncode == 2
    assert run_tools(site, "topo", "--bogus").returncode == 2
    assert site["log"].read_text() == ""


@pytest.fixture
def checkout(site) -> Path:
    chk = site["tmp"] / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for rel in (
        "nplb",
        "topo_tools.sh",
        "topo_map.py",
        "topology_summary.py",
        "pages.py",
        "lib/pages.sh",
    ):
        shutil.copy(NPLB / rel, nplb_dir / rel)
    return chk


def test_dispatcher_topo_verb(site, checkout):
    nplb = checkout / "examples" / "nplb" / "nplb"
    result = run_tools(site, "topo", "--from", str(site["tsv"]), script=nplb)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("topo | page 1/")
    assert lines[1] == "VERDICT: OK leaves known for 4 of 4 nodes"
    assert site["log"].read_text() == ""

    helped = run_tools(site, "help", script=nplb)
    assert helped.returncode == 0
    help_lines = helped.stdout.splitlines()
    assert any(ln.startswith("  topo") for ln in help_lines)
    assert 0 < len(help_lines) <= 38
    assert max(len(ln) for ln in help_lines) <= 100
