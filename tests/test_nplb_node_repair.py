"""examples/nplb/node_repair.sh (`nplb node fix --links|--peermem|--nfs-head|--rescan`), run with bash
against fake nodes.

The fake nodes of tests/test_nplb_node_diag.py (hosts/<name>/bin with fake tools that log to
calls.log) get stateful tools here: `ip link set X up` flips a port to Up in the fake ibdev2netdev,
`modprobe` makes lsmod list nvidia_peermem, a rescan makes lspci list the card, a restart plus
`exportfs -ra` makes `exportfs -v` list the export. Every repair command also lands in one shared
order log. The PCI rescan file is a path under tmp_path (PCI_RESCAN_PATH): nothing here can write
the real /sys/bus/pci/rescan. No real ssh, modprobe, systemctl or network."""

# ruff: noqa: F811, F401  (the `site` fixture is imported from test_nplb_node_diag and taken by name)
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from test_nplb_node_diag import (
    CX8_PORTS,
    FAKE_MOUNT,
    HEAD,
    NFS,
    SCRIPT,
    calls,
    lspci_text,
    make_exe,
    make_host,
    netdev,
    run,
    saved,
    site,
)

FAKE_IBDEV = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "ibdev2netdev $*" >>"$hd/calls.log"
awk -v f="$hd/up_set" 'BEGIN { while ((getline l < f) > 0) up[l] = 1 }
    { if ($0 ~ /\(Down\)/ && ($5 in up)) sub(/\(Down\)/, "(Up)"); print }' "$hd/data/ibdev2netdev.out"
"""
FAKE_IP = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "ip $*" >>"$hd/calls.log"
if [[ $1 == link && $2 == set ]]; then
    echo "$(basename "$hd") ip $*" >>"${FAKE_ORDER_LOG:-/dev/null}"
    [[ -e $hd/link_set_fails ]] && { echo "RTNETLINK answers: Operation not permitted" >&2; exit 2; }
    [[ -e $hd/link_stuck ]] || echo "$3" >>"$hd/up_set"
    exit 0
fi
cat "$hd/data/ip.out"
"""
FAKE_LSMOD = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "lsmod $*" >>"$hd/calls.log"
cat "$hd/data/lsmod.out"
cat "$hd/loaded" 2>/dev/null
exit 0
"""
FAKE_MODPROBE = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "modprobe $*" >>"$hd/calls.log"
echo "$(basename "$hd") modprobe $*" >>"${FAKE_ORDER_LOG:-/dev/null}"
if [[ -e $hd/modprobe_fails ]]; then
    echo "modprobe: FATAL: Module nvidia_peermem not found in directory /lib/modules/6.8" >&2
    echo "second stderr line" >&2
    exit 1
fi
echo "nvidia_peermem 16384 0" >>"$hd/loaded"
"""
FAKE_LSPCI = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "lspci $*" >>"$hd/calls.log"
if [[ -e $hd/rescanned && -f $hd/data/lspci_after.out ]]; then
    cat "$hd/data/lspci_after.out"
else
    cat "$hd/data/lspci.out"
fi
"""
# Stands in for sh: logs a rescan command, notes that it ran, then runs the real sh (whose redirect
# goes to PCI_RESCAN_PATH, a file under tmp_path).
FAKE_SH = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $1 == -c && $2 == *rescan* ]]; then
    echo "sh $*" >>"$hd/calls.log"
    echo "$(basename "$hd") sh $*" >>"${FAKE_ORDER_LOG:-/dev/null}"
    touch "$hd/rescanned"
fi
exec "${FAKE_REAL_SH:?}" "$@"
"""
FAKE_RESTART = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "TOOL $*" >>"$hd/calls.log"
echo "$(basename "$hd") TOOL $*" >>"${FAKE_ORDER_LOG:-/dev/null}"
touch "$hd/restarted"
exit 0
"""
FAKE_EXPORTFS = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "exportfs $*" >>"$hd/calls.log"
if [[ $1 == -ra ]]; then
    echo "$(basename "$hd") exportfs $*" >>"${FAKE_ORDER_LOG:-/dev/null}"
    [[ -e $hd/restarted && -e $hd/export_works ]] && touch "$hd/exported"
    exit 0
fi
if [[ -e $hd/exported ]]; then
    cat "$hd/data/exported.out"
else
    cat "$hd/data/exportfs.out"
fi
"""


def repair_host(
    site: dict,
    name: str,
    *,
    down: list[str] | None = None,
    stuck: bool = False,
    link_set_fails: bool = False,
    peermem: bool = True,
    modprobe_fails: bool = False,
    cx8: int = 16,
    cx8_after: int | None = None,
    exported: bool = True,
    export_works: bool = True,
    systemctl: bool = True,
    mounted: bool = True,
) -> Path:
    """A fake node whose repair tools keep state; the flags choose what is wrong and what works."""
    hd = make_host(site, name, down=down, cx8=cx8, mounted=mounted)
    (hd / "up_set").unlink(missing_ok=True)
    for stale in ("rescanned", "restarted", "exported", "loaded"):
        (hd / stale).unlink(missing_ok=True)
    data = hd / "data"
    lsmod = "Module                  Size  Used by\nnvidia               9999999  100\n"
    if peermem:
        lsmod += "nvidia_peermem         16384  0\n"
    (data / "lsmod.out").write_text(lsmod)
    if cx8_after is not None:
        (data / "lspci_after.out").write_text(lspci_text(cx8_after))
    (data / "exported.out").write_text(f"{NFS}\t\t*(sync,wdelay,hide,no_subtree_check,rw)\n")
    if not exported:
        (data / "exportfs.out").write_text("/srv/other\t\t10.9.9.9(rw)\n")
    else:
        (hd / "exported").write_text("")
    for flag, on in (
        ("link_stuck", stuck),
        ("link_set_fails", link_set_fails),
        ("modprobe_fails", modprobe_fails),
        ("export_works", export_works),
    ):
        (hd / flag).unlink(missing_ok=True)
        if on:
            (hd / flag).write_text("")
    bin_dir = hd / "bin"
    make_exe(bin_dir / "ibdev2netdev", FAKE_IBDEV)
    make_exe(bin_dir / "ip", FAKE_IP)
    make_exe(bin_dir / "lsmod", FAKE_LSMOD)
    make_exe(bin_dir / "modprobe", FAKE_MODPROBE)
    make_exe(bin_dir / "lspci", FAKE_LSPCI)
    make_exe(bin_dir / "sh", FAKE_SH)
    make_exe(bin_dir / "exportfs", FAKE_EXPORTFS)
    make_exe(bin_dir / "service", FAKE_RESTART.replace("TOOL", "service"))
    if systemctl:
        make_exe(bin_dir / "systemctl", FAKE_RESTART.replace("TOOL", "systemctl"))
    make_exe(
        bin_dir / "mount",
        FAKE_MOUNT.replace(
            'echo "mount $*" >>"$hd/calls.log"',
            'echo "mount $*" >>"$hd/calls.log"\n'
            'echo "$(basename "$hd") mount $*" >>"${FAKE_ORDER_LOG:-/dev/null}"',
        ),
    )
    return hd


def fixrun(
    site: dict, *args: str, yes: bool = True, **env: str
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """`node_tools.sh fix ARGS` with the waits at 0 and the rescan file under tmp_path."""
    tmp = site["tmp"]
    env.setdefault("FIX_LINK_WAIT", "0")
    env.setdefault("FIX_RESCAN_WAIT", "0")
    env.setdefault("PCI_RESCAN_PATH", str(tmp / "pci_rescan"))
    env.setdefault("FAKE_REAL_SH", shutil.which("sh") or "/bin/sh")
    env.setdefault("FAKE_ORDER_LOG", str(tmp / "order.log"))
    if yes:
        env.setdefault("NPLB_YES", "1")
    result = run(site, "fix", *args, **env)
    try:
        lines = saved(site, "node-fix") if result.returncode in (0, 1) and result.stdout else []
    except AssertionError:
        lines = []
    return result, lines


def order(site: dict) -> list[str]:
    log = site["tmp"] / "order.log"
    return log.read_text().splitlines() if log.exists() else []


def tools_called(site: dict, host: str) -> list[str]:
    return [c.split()[0] for c in calls(site, host)]


def no_repair_calls(site: dict, host: str) -> bool:
    bad = ("ip link set", "modprobe", "sh -c", "systemctl", "service", "exportfs -ra")
    return not [c for c in calls(site, host) if c.startswith(bad)]


def has(lines: list[str], text: str) -> bool:
    return any(text in ln for ln in lines)


# ---- --links ----


def test_links_brings_down_ports_up_and_reads_back(site):
    down = ["mlx5_4", "mlx5_5"]
    repair_host(site, "node112", down=down)
    result, lines = fixrun(site, "--links", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK fix"
    for dev in down:
        assert calls(site, "node112").count(f"ip link set {netdev(dev)} up") == 1
    assert "OK node112 links: mlx5_4 mlx5_5 are Up now" in lines
    assert "Proceed? [y/N]" in result.stderr
    assert "ip link set" in result.stderr  # the plan names the command
    for entry in site["ssh_log"].read_text().splitlines():
        assert entry.split(" ", 1)[1].startswith("timeout 20 "), entry


def test_links_single_port_wording(site):
    repair_host(site, "node112", down=["mlx5_4"])
    _, lines = fixrun(site, "--links", "node112")
    assert "OK node112 links: mlx5_4 is Up now" in lines


def test_links_all_up_runs_no_command(site):
    repair_host(site, "node112")
    result, lines = fixrun(site, "--links", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert no_repair_calls(site, "node112")
    assert "OK node112 links: all 16 ports Up, nothing to do" in lines


def test_links_port_that_stays_down_is_a_hardware_fail(site):
    repair_host(site, "node112", down=["mlx5_4"], stuck=True)
    result, lines = fixrun(site, "--links", "node112")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "FAIL node112 links: mlx5_4 still not Up: no signal from the far end: cable or switch port, "
        "a hardware call (run nplb node diag node112)"
    ) in lines
    assert lines[0] == "VERDICT: FAIL fix (links node112)"


def test_links_ip_command_failure_names_its_message(site):
    repair_host(site, "node112", down=["mlx5_4"], link_set_fails=True)
    result, lines = fixrun(site, "--links", "node112")
    assert result.returncode == 1, result.stdout + result.stderr
    assert has(lines, "Operation not permitted")
    assert lines[0].startswith("VERDICT: FAIL fix")


def test_links_declined_runs_nothing(site):
    repair_host(site, "node112", down=["mlx5_4"])
    result, _ = fixrun(site, "--links", "node112", yes=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "declined: nothing changed" in result.stderr
    assert "ip link set" in result.stderr  # the plan was shown
    assert not calls(site, "node112")
    assert not site["ssh_log"].exists()


def test_links_leaves_up_ports_alone_while_fixing_the_down_one(site):
    repair_host(site, "node112", down=["mlx5_9"])
    result, _ = fixrun(site, "--links", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    sets = [c for c in calls(site, "node112") if c.startswith("ip link set")]
    assert sets == [f"ip link set {netdev('mlx5_9')} up"]


def test_links_many_down_ports_fit_a_page(site):
    repair_host(site, "node112", down=CX8_PORTS, stuck=True)
    repair_host(site, "node114", down=CX8_PORTS, stuck=True)
    result, lines = fixrun(site, "--links", "node112", "node114")
    out = result.stdout.splitlines()
    assert result.returncode == 1
    assert 0 < len(out) <= 38
    assert max(len(ln) for ln in out) <= 100
    assert len(lines) <= 30


# ---- --peermem ----


def test_peermem_loads_the_module_and_reads_back(site):
    repair_host(site, "node112", peermem=False)
    result, lines = fixrun(site, "--peermem", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(site, "node112").count("modprobe nvidia_peermem") == 1
    assert "OK node112 peermem: nvidia_peermem is loaded now" in lines
    assert lines[0] == "VERDICT: OK fix"
    assert "modprobe nvidia_peermem" in result.stderr  # the plan


def test_peermem_already_loaded_runs_no_modprobe(site):
    repair_host(site, "node112", peermem=True)
    result, lines = fixrun(site, "--peermem", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert no_repair_calls(site, "node112")
    assert "OK node112 peermem: nvidia_peermem is already loaded, nothing to do" in lines


def test_peermem_modprobe_failure_shows_its_first_stderr_line(site):
    repair_host(site, "node112", peermem=False, modprobe_fails=True)
    result, lines = fixrun(site, "--peermem", "node112")
    assert result.returncode == 1, result.stdout + result.stderr
    fail = next(ln for ln in lines if ln.startswith("FAIL node112 peermem:"))
    assert "Module nvidia_peermem not found" in fail
    assert "second stderr line" not in fail
    assert lines[0] == "VERDICT: FAIL fix (peermem node112)"


def test_peermem_declined_runs_nothing(site):
    repair_host(site, "node112", peermem=False)
    result, _ = fixrun(site, "--peermem", "node112", yes=False)
    assert result.returncode == 1
    assert "declined: nothing changed" in result.stderr
    assert not calls(site, "node112")


# ---- --nfs-head ----


def test_nfs_head_restarts_the_server_and_reads_the_export_back(site):
    repair_host(site, HEAD, exported=False)
    result, lines = fixrun(site, "--nfs-head", HEAD)
    assert result.returncode == 0, result.stdout + result.stderr
    head = calls(site, HEAD)
    assert head.count("systemctl restart nfs-server") == 1
    assert head.count("exportfs -ra") == 1
    assert head.index("systemctl restart nfs-server") < head.index("exportfs -ra")
    assert f"OK head nfs-server: {NFS} is exported now" in lines
    assert lines[0] == "VERDICT: OK fix"
    assert "systemctl restart nfs-server" in result.stderr  # the plan


def test_nfs_head_without_systemctl_uses_service(site):
    repair_host(site, HEAD, exported=False, systemctl=False)
    result, lines = fixrun(site, "--nfs-head", HEAD)
    assert result.returncode == 0, result.stdout + result.stderr
    head = calls(site, HEAD)
    assert head.count("service nfs-kernel-server restart") == 1
    assert "systemctl restart nfs-server" not in head
    assert has(lines, f"{NFS} is exported now")


def test_nfs_head_already_exported_restarts_nothing(site):
    repair_host(site, HEAD, exported=True)
    result, lines = fixrun(site, "--nfs-head", HEAD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert no_repair_calls(site, HEAD)
    assert f"OK head nfs-server: {NFS} is already exported, nothing to do" in lines


def test_nfs_head_still_not_exported_prints_the_line_for_the_lab(site):
    repair_host(site, HEAD, exported=False, export_works=False)
    result, lines = fixrun(site, "--nfs-head", HEAD)
    assert result.returncode == 1, result.stdout + result.stderr
    assert has(lines, f"FAIL head nfs-server: {NFS} is still not exported")
    assert f"  {NFS} *(rw,sync,no_root_squash,no_subtree_check)" in lines
    assert "  then: exportfs -ra" in lines
    assert "head nfs-server" in lines[0]


def test_nfs_head_declined_runs_nothing(site):
    repair_host(site, HEAD, exported=False)
    result, _ = fixrun(site, "--nfs-head", HEAD, yes=False)
    assert result.returncode == 1
    assert "declined: nothing changed" in result.stderr
    assert not calls(site, HEAD)


def test_nfs_head_runs_on_the_head_only_and_never_writes_exports(site):
    repair_host(site, HEAD, exported=False)
    repair_host(site, "node112")
    result, _ = fixrun(site, "--nfs-head", HEAD, "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert no_repair_calls(site, "node112")
    assert not [c for c in calls(site, "node112") if c.startswith("exportfs")]
    for host in (HEAD, "node112"):
        assert not [c for c in calls(site, host) if "etc/exports" in c], host
    assert not any("etc/exports" in entry for entry in site["ssh_log"].read_text().splitlines())
    text = (SCRIPT.parent / "node_repair.sh").read_text()
    assert not re.search(r"(>|tee\s|sed -i)[^\n]*etc/exports", text)


def test_nfs_head_runs_on_the_head_even_when_only_workers_are_named(site):
    repair_host(site, HEAD, exported=False)
    repair_host(site, "node112")
    result, _ = fixrun(site, "--nfs-head", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(site, HEAD).count("systemctl restart nfs-server") == 1
    assert no_repair_calls(site, "node112")


# ---- --rescan ----


def test_rescan_finds_the_card_and_reads_the_count_back(site):
    repair_host(site, "node112", cx8=14, cx8_after=16)
    result, lines = fixrun(site, "--rescan", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    rescans = [c for c in calls(site, "node112") if c.startswith("sh -c") and "rescan" in c]
    assert len(rescans) == 1
    assert (site["tmp"] / "pci_rescan").read_text().strip() == "1"
    assert "OK node112 rescan: count now 16/16 CX8 functions" in lines
    assert "rescan is harmless on a running node; a missing card usually stays missing" in (
        result.stderr
    )
    # the redirection is inside the remote command, as one quoted argument of sh -c
    sent = [e for e in site["ssh_log"].read_text().splitlines() if "rescan" in e]
    assert len(sent) == 1 and sent[0].startswith("node112 timeout 20 sh -c ")


def test_rescan_full_count_runs_nothing(site):
    repair_host(site, "node112", cx8=16)
    result, lines = fixrun(site, "--rescan", "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert no_repair_calls(site, "node112")
    assert not (site["tmp"] / "pci_rescan").exists()
    assert "OK node112 rescan: 16/16 CX8 functions already, nothing to do" in lines


def test_rescan_that_changes_nothing_is_a_hardware_fail(site):
    repair_host(site, "node112", cx8=14, cx8_after=None)
    result, lines = fixrun(site, "--rescan", "node112")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "FAIL node112 rescan: still 14 of 16 CX8 functions: the card is not seen by the machine, "
        "a hardware call for the lab"
    ) in lines
    assert lines[0] == "VERDICT: FAIL fix (rescan node112)"


def test_rescan_declined_runs_nothing(site):
    repair_host(site, "node112", cx8=14, cx8_after=16)
    result, _ = fixrun(site, "--rescan", "node112", yes=False)
    assert result.returncode == 1
    assert "declined: nothing changed" in result.stderr
    assert not calls(site, "node112")
    assert not (site["tmp"] / "pci_rescan").exists()


# ---- all together ----


def test_flags_run_in_the_documented_order(site):
    repair_host(site, HEAD, exported=False)
    repair_host(
        site, "node112", down=["mlx5_4"], peermem=False, cx8=14, cx8_after=16, mounted=False
    )
    result, lines = fixrun(site, "--nfs-head", "--peermem", "--links", "--rescan", HEAD, "node112")
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK fix"
    wanted = (
        "node112 sh -c",
        "node112 ip link set",
        "node112 modprobe nvidia_peermem",
        f"{HEAD} systemctl restart nfs-server",
        f"{HEAD} exportfs -ra",
        "node112 mount -t nfs",
    )
    log = order(site)
    positions = []
    for want in wanted:
        hits = [i for i, entry in enumerate(log) if entry.startswith(want)]
        assert len(hits) == 1, (want, log)
        positions.append(hits[0])
    assert positions == sorted(positions), log
    plan = result.stderr
    assert (
        plan.index("ip link set")
        < plan.index("modprobe nvidia_peermem")
        < plan.index("systemctl restart nfs-server")
    )
    assert plan.index("rescan") < plan.index("ip link set")


def test_host_names_cannot_inject_a_command(site):
    for host in ("node112;touch PWNED", "node 112 && touch PWNED", "$(touch PWNED)"):
        result, lines = fixrun(
            site, "--links", "--peermem", "--rescan", "--nfs-head", host, "node112"
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert has(lines, "cannot reach"), lines
    assert not (site["tmp"] / "PWNED").exists()
    assert not list(site["tmp"].rglob("PWNED"))


def test_help_and_usage_list_the_new_flags(site):
    helped = run(site, "fix", "--help")
    assert helped.returncode == 0
    for flag in ("--links", "--peermem", "--nfs-head", "--rescan"):
        assert flag in helped.stderr
    bad = run(site, "fix", "--frob", NPLB_YES="1")
    assert bad.returncode == 2
    for flag in ("--links", "--peermem", "--nfs-head", "--rescan"):
        assert flag in bad.stderr
    assert not site["ssh_log"].exists()
