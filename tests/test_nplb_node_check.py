"""examples/nplb/node_tools.sh (`nplb node check`), run with bash against a fake gpu_preflight.sh.

Everything lives under tmp_path: a fake preflight script that prints a canned text and logs its
arguments, a fake df on PATH, and a PATH that holds nothing else but symlinks to the few real tools
the script needs. No ssh, no network, no Slurm.

The pager cuts a line at 100 columns, so details are asserted on the report the pager saves whole."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SCRIPT = NPLB / "node_tools.sh"
BASH = shutil.which("bash") or "/bin/bash"
NOW = "2026-11-09T14:07:00"
HEAD = "node110"
HOSTS = ["node110", "node112", "node114", "node116"]
CHECKS = ["state", "ports", "gpus", "peermem", "mpirun", "ucx", "blink", "nfs", "sr"]
TOOLS = (
    "bash sh awk sed sort grep head tail cat date mkdir mv dirname basename uname python3 "
    "find tr cut wc ls stat env readlink mktemp touch chmod ln tee cp id sleep rm"
).split()

FAKE_PREFLIGHT = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_PREFLIGHT_LOG"
cat "$FAKE_PREFLIGHT_OUT"
if [[ -n ${FAKE_PREFLIGHT_STDERR:-} ]]; then
    echo "$FAKE_PREFLIGHT_STDERR" >&2
fi
exit "${FAKE_PREFLIGHT_RC:-0}"
"""
FAKE_DF = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_DF_LOG"
echo "Filesystem     1024-blocks      Used Available Capacity Mounted on"
echo "/dev/fake       1000000000 500000000 $((FAKE_DF_GIB * 1048576)) 50% /fake"
"""


def make_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def preflight_text(
    hosts: list[str] = HOSTS,
    checks: list[str] = CHECKS,
    overrides: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> str:
    """What gpu_preflight.sh prints: a line per host and check (nfs only on workers), then a summary."""
    overrides = overrides or {}
    lines = []
    for host in hosts:
        for check in checks:
            if check == "nfs" and host == HEAD:
                continue
            level, detail = overrides.get((host, check), ("OK", f"{check} is fine"))
            lines.append(f"{host:<7} {check:<8} {level:<4} {detail}")
    lines.append(f"gpu_preflight: 0 FAIL, 0 WARN on {len(hosts)} hosts")
    return "\n".join(lines) + "\n"


@pytest.fixture
def site(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        real = shutil.which(name)
        if real:
            (tools / name).symlink_to(real)
    make_exe(bin_dir / "df", FAKE_DF)
    make_exe(tmp_path / "gpu_preflight.sh", FAKE_PREFLIGHT)
    (tmp_path / "crab" / "data").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    site = {
        "tmp": tmp_path,
        "bin": bin_dir,
        "tools": tools,
        "preflight": tmp_path / "gpu_preflight.sh",
        "out": tmp_path / "preflight.out",
        "crab": tmp_path / "crab",
        "reports": tmp_path / "reports",
        "pre_log": tmp_path / "preflight.log",
        "df_log": tmp_path / "df.log",
    }
    site["out"].write_text(preflight_text())
    return site


def env_for(site: dict, **extra: str) -> dict:
    env = {
        "PATH": f"{site['bin']}:{site['tools']}",
        "HOME": str(site["tmp"] / "home"),
        "NPLB_REPORT_DIR": str(site["reports"]),
        "NPLB_NOW": NOW,
        "GPU_PREFLIGHT": str(site["preflight"]),
        "FAKE_PREFLIGHT_OUT": str(site["out"]),
        "FAKE_PREFLIGHT_LOG": str(site["pre_log"]),
        "FAKE_DF_LOG": str(site["df_log"]),
        "FAKE_DF_GIB": "100",
        "CRAB_DIR": str(site["crab"]),
        "HEAD": HEAD,
        "PYTHONPATH": "/bad/pythonpath",
    }
    env.update(extra)
    return env


def run(site: dict, *args: str, script: Path = SCRIPT, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(script), *args],
        env=env_for(site, **env),
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def check_cmd(site: dict, *args: str, **env: str) -> subprocess.CompletedProcess:
    return run(site, "check", *args, **env)


def saved(site: dict, name: str = "node-check") -> list[str]:
    found = sorted(site["reports"].glob(f"*_{name}*.txt"), key=os.path.getmtime)
    assert found, f"no saved report {name}"
    return found[-1].read_text().splitlines()


def content(stdout: str) -> list[str]:
    return stdout.splitlines()[1:-1]


def grid(lines: list[str]) -> dict[str, dict[str, str]]:
    """host (as printed) -> {column header: cell}, read from the grid under the verdict."""
    header = next(i for i, ln in enumerate(lines) if ln.split()[:1] == ["host"])
    names = lines[header].split()[1:]
    rows = {}
    for ln in lines[header + 1 :]:
        if not ln.strip():
            break
        parts = ln.split()
        rows[parts[0]] = dict(zip(names, parts[1:], strict=True))
    return rows


# ---- the verdict and the grid ----


def test_all_ok_four_nodes(site):
    result = check_cmd(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("node-check |")
    assert content(result.stdout)[0] == "VERDICT: OK"
    lines = saved(site)
    assert lines[0] == "VERDICT: OK"
    cells = grid(lines)
    assert list(cells) == HOSTS
    columns = ["state", "ports", "gpus", "peer", "mpi", "ucx", "blink", "nfs", "sr", "disk"]
    for row in cells.values():
        assert list(row) == columns
        assert set(row.values()) <= {"ok", "--"}
    # nfs is checked on workers only, disk on the head only
    assert cells[HEAD]["nfs"] == "--" and cells[HEAD]["disk"] == "ok"
    for worker in HOSTS[1:]:
        assert cells[worker]["nfs"] == "ok" and cells[worker]["disk"] == "--"
        assert all(v == "ok" for k, v in cells[worker].items() if k != "disk")
    assert cells[HEAD]["ports"] == "ok"
    assert not any(" WARN " in ln or " FAIL " in ln for ln in lines)


def test_a_failing_port_check_names_the_node_first(site):
    detail = "only 8 of 16 ports ACTIVE"
    site["out"].write_text(preflight_text(overrides={("node110", "ports"): ("FAIL", detail)}))
    result = check_cmd(site)
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL node110 (ports)"
    lines = saved(site)
    assert lines[0] == "VERDICT: FAIL node110 (ports)"
    cells = grid(lines)
    assert cells["node110"]["ports"] == "FAIL"
    assert cells["node112"]["ports"] == "ok"
    assert f"node110 ports FAIL {detail}" in lines
    assert lines[lines.index("") + 1 :] == [f"node110 ports FAIL {detail}"]


def test_failing_checks_of_several_hosts_are_all_named(site):
    site["out"].write_text(
        preflight_text(
            overrides={
                ("node110", "ports"): ("FAIL", "no cards"),
                ("node110", "gpus"): ("FAIL", "0 GPUs"),
                ("node114", "peermem"): ("FAIL", "not loaded"),
            }
        )
    )
    result = check_cmd(site)
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL node110 (ports, gpus); node114 (peermem)"


def test_a_long_verdict_is_cut_with_dots(site):
    overrides = {(h, c): ("FAIL", "bad") for h in HOSTS for c in CHECKS}
    site["out"].write_text(preflight_text(overrides=overrides))
    result = check_cmd(site)
    assert result.returncode == 1
    first = saved(site)[0]
    assert first.startswith("VERDICT: FAIL node110 (state, ports")
    assert first.endswith("...") and len(first) <= 90


def test_warn_only_is_ok_with_a_note(site):
    site["out"].write_text(
        preflight_text(overrides={("node114", "sr"): ("WARN", "SR=1 AR=0 but SR mixed")})
    )
    result = check_cmd(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert content(result.stdout)[0] == "VERDICT: OK, WARN 1 (node114 sr)"
    lines = saved(site)
    assert grid(lines)["node114"]["sr"] == "WARN"
    assert "node114 sr WARN SR=1 AR=0 but SR mixed" in lines


def test_unreachable_host_is_a_fail_row(site):
    # gpu_preflight.sh prints exactly one "<host> ssh FAIL <detail>" line for a host it cannot reach
    text = (
        preflight_text(hosts=HOSTS[:3])
        + "node117  ssh      FAIL ssh: connect to host node117: refused\n"
    )
    site["out"].write_text(text)
    result = check_cmd(site)
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL node117 (ssh)"
    lines = saved(site)
    cells = grid(lines)
    assert list(cells) == [*HOSTS[:3], "node117"]
    assert cells["node117"]["ssh"] == "FAIL"
    assert cells["node112"]["ssh"] == "ok"
    for check in ("state", "ports", "gpus", "peer", "mpi", "ucx", "blink", "nfs", "sr"):
        assert cells["node117"][check] == "FAIL", check
    assert cells["node117"]["disk"] == "--"
    assert "node117 ssh FAIL ssh: connect to host node117: refused" in lines


def test_disk_check(site):
    result = check_cmd(site, FAKE_DF_GIB="100")
    assert result.returncode == 0
    # df reads the checkout's data folder through the fake df
    assert site["df_log"].read_text().split() == ["-Pk", str(site["crab"] / "data")]

    result = check_cmd(site, FAKE_DF_GIB="12")
    assert result.returncode == 0
    assert content(result.stdout)[0] == "VERDICT: OK, WARN 1 (node110 disk)"
    lines = saved(site)
    assert grid(lines)["node110"]["disk"] == "WARN"
    assert f"node110 disk WARN 12 GiB free on {site['crab']}/data" in lines

    result = check_cmd(site, FAKE_DF_GIB="3")
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL node110 (disk)"
    assert f"node110 disk FAIL 3 GiB free on {site['crab']}/data" in saved(site)

    result = check_cmd(site, FAKE_DF_GIB="12", NODE_MIN_GIB="10")
    assert result.returncode == 0
    assert content(result.stdout)[0] == "VERDICT: OK"


def test_disk_falls_back_to_the_checkout(site):
    (site["crab"] / "data").rmdir()
    result = check_cmd(site, FAKE_DF_GIB="9")
    assert result.returncode == 0
    assert site["df_log"].read_text().split() == ["-Pk", str(site["crab"])]
    assert f"node110 disk WARN 9 GiB free on {site['crab']}" in saved(site)


def test_grid_fits_a_page_for_many_checks(site):
    checks = [*CHECKS, "bandwidth", "somethingelse"]
    hosts = ["node110", "node112", "node114", "node11678901234"]
    site["out"].write_text(
        preflight_text(
            hosts=hosts,
            checks=checks,
            overrides={("node11678901234", "bandwidth"): ("WARN", "slow link")},
        )
    )
    result = check_cmd(site)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = saved(site)
    header = next(ln for ln in lines if ln.split()[:1] == ["host"])
    rows = [ln for ln in lines if ln.split()[:1] == ["node11678~"]]
    assert len(rows) == 1
    assert "bandwi" in header and "someth" in header
    for ln in (header, *rows):
        assert len(ln) <= 100
    assert len(grid(lines)) == 4
    assert "node11678901234 bandwidth WARN slow link" in lines
    assert 0 < len(result.stdout.splitlines()) <= 38
    assert max(len(ln) for ln in result.stdout.splitlines()) <= 100


def test_long_reports_are_paged(site):
    overrides = {(h, c): ("WARN", "x" * 120) for h in HOSTS for c in CHECKS}
    site["out"].write_text(preflight_text(overrides=overrides))
    result = check_cmd(site)
    assert result.returncode == 0
    printed = result.stdout.splitlines()
    assert len(printed) <= 38 and max(len(ln) for ln in printed) <= 100
    assert len(saved(site)) > 38
    assert any("x" * 120 in ln for ln in saved(site))


# ---- hosts and the preflight ----


def test_hosts_default_and_args(site):
    check_cmd(site, "node114", "node110")
    assert site["pre_log"].read_text().splitlines() == ["node114 node110"]

    site["pre_log"].unlink()
    site["out"].write_text(preflight_text(hosts=["node110", "node112", "node114"]))
    result = check_cmd(site, PARTITION="h100_4nodes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert site["pre_log"].read_text().splitlines() == [""]
    assert list(grid(saved(site))) == ["node110", "node112", "node114"]


def test_a_requested_host_without_a_line_is_a_fail_row(site):
    site["out"].write_text(preflight_text(hosts=["node110", "node112"]))
    result = check_cmd(site, "node110", "node112", "node114")
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL node114 (ssh)"
    assert "node114 ssh FAIL gpu_preflight printed no line for this host" in saved(site)


def test_head_outside_the_hosts_skips_disk(site):
    site["out"].write_text(preflight_text(hosts=["node112", "node114"]))
    result = check_cmd(site, "node112", "node114")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not site["df_log"].exists()
    assert "disk" not in next(ln for ln in saved(site) if ln.startswith("host"))
    assert "disk not checked" in result.stderr


def test_preflight_missing_or_silent(site):
    missing = site["tmp"] / "nowhere.sh"
    result = check_cmd(site, GPU_PREFLIGHT=str(missing))
    assert result.returncode == 2
    assert str(missing) in result.stderr
    assert not site["reports"].exists()

    site["out"].write_text("")
    result = check_cmd(
        site, FAKE_PREFLIGHT_STDERR="gpu_preflight: sinfo lists no nodes", FAKE_PREFLIGHT_RC="2"
    )
    assert result.returncode == 1
    assert content(result.stdout)[0] == "VERDICT: FAIL no output from gpu_preflight"
    assert "gpu_preflight said: gpu_preflight: sinfo lists no nodes" in saved(site)
    assert not site["df_log"].exists()


def test_preflight_exit_code_does_not_decide_the_verdict(site):
    # gpu_preflight.sh exits 1 on any FAIL; the grid is built from its lines, not its exit code
    result = check_cmd(site, FAKE_PREFLIGHT_RC="1")
    assert result.returncode == 0
    assert content(result.stdout)[0] == "VERDICT: OK"


def test_narrates_on_stderr_and_report_on_stdout(site):
    result = check_cmd(site)
    assert result.returncode == 0, result.stdout + result.stderr
    err = result.stderr.splitlines()
    assert err and all(re.match(r"\[\d\d:\d\d:\d\d\] ", ln) for ln in err), result.stderr
    assert re.search(r"\] running gpu_preflight", err[0])
    assert any("checking free disk" in ln for ln in err)
    out = result.stdout.splitlines()
    assert out[0].startswith("node-check |")
    assert not any(re.match(r"\[\d\d:\d\d:\d\d\]", ln) for ln in out)


def test_usage_errors(site):
    for args in ((), ("frob",)):
        bad = run(site, *args)
        assert bad.returncode == 2
        assert "usage: node_tools.sh check [HOST...]" in bad.stderr
    assert not site["pre_log"].exists()


# ---- the dispatcher ----


@pytest.fixture
def checkout(site) -> Path:
    chk = site["tmp"] / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for rel in ("nplb", "node_tools.sh", "node_diag.sh", "node_fix.sh", "pages.py", "lib/pages.sh"):
        shutil.copy(NPLB / rel, nplb_dir / rel)
    return chk


def test_dispatcher_node_verb(site, checkout):
    nplb = checkout / "examples" / "nplb" / "nplb"
    result = run(site, "node", "check", "node112", script=nplb)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("node-check |")
    assert content(result.stdout)[0] == "VERDICT: OK"
    assert site["pre_log"].read_text().splitlines() == ["node112"]

    for args in ((), ("frob",)):
        bad = run(site, "node", *args, script=nplb)
        assert bad.returncode == 2
        assert "usage: nplb node check [HOST...]" in bad.stderr

    helped = run(site, "help", script=nplb)
    assert helped.returncode == 0
    lines = helped.stdout.splitlines()
    assert any(ln.startswith("  node check") for ln in lines)
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
