"""`nplb diag counters snapshot|diff` (examples/nplb/diag_counters.sh), run against a stub gpu_preflight.sh.

The stub prints the shapes of the real `gpu_preflight.sh counters snapshot|diff` (see its source) and logs its
arguments; everything lives under tmp_path. No ssh, no node, no network. The script also runs through the
dispatcher in a fake checkout. The pager cuts lines at 100 columns, so details are asserted on the report
the pager saves whole.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NPLB = REPO / "examples" / "nplb"
SCRIPT = NPLB / "diag_counters.sh"
PROXY = (
    "proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch"
)
ECN = "np_ecn_marked_roce_packets"
USAGE = "usage: nplb diag counters snapshot|diff [HOST...]"

STUB = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
if [[ ${1:-} == counters && ${2:-} == snapshot ]]; then
    mkdir -p "$(dirname "$3")"
    echo "stubhost mlx5_0 np_cnp_sent 0" >"$3"
fi
cat "$FAKE_OUT"
if [[ -n ${FAKE_ERR:-} ]]; then
    echo "$FAKE_ERR" >&2
fi
exit "${FAKE_RC:-0}"
"""


def snapshot_file(tmp_path: Path) -> Path:
    return tmp_path / "work" / "counters_snapshot.txt"


def make_stub(tmp_path: Path) -> Path:
    stub = tmp_path / "stub" / "gpu_preflight.sh"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def write_snapshot(tmp_path: Path, hosts: list[str]) -> Path:
    path = snapshot_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{h} mlx5_0 np_cnp_sent 0\n" for h in hosts))
    return path


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "PAGE_LINES", "PAGE_COLS", "DIAG_NOW"):
        env.pop(var, None)
    env["WORK"] = str(tmp_path / "work")
    env["NPLB_REPORT_DIR"] = str(tmp_path / "reports")
    env["GPU_PREFLIGHT"] = str(make_stub(tmp_path))
    env["FAKE_LOG"] = str(tmp_path / "stub.log")
    env["FAKE_OUT"] = str(tmp_path / "stub.out")
    env.update(extra)
    return env


def call(
    tmp_path: Path,
    args: list[str],
    out: str = "",
    rc: int = 0,
    err: str = "",
    checkout: Path | None = None,
    **env: str,
) -> subprocess.CompletedProcess[str]:
    """Run `diag_counters.sh ARGS` (or `nplb diag counters ARGS` with CHECKOUT) with the stub's canned reply."""
    (tmp_path / "stub.out").write_text(out)
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    cmd = ["bash", str(SCRIPT), *args]
    if checkout is not None:
        cmd = ["bash", str(checkout / "examples" / "nplb" / "nplb"), "diag", "counters", *args]
    return subprocess.run(
        cmd,
        env=env_for(tmp_path, FAKE_RC=str(rc), FAKE_ERR=err, **env),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def stub_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "stub.log"
    return log.read_text().splitlines() if log.exists() else []


def saved(tmp_path: Path, name: str) -> list[str]:
    """The whole saved report called NAME (never the paged screen)."""
    found = sorted((tmp_path / "reports").glob(f"*_{name}.txt"))
    assert len(found) == 1, (
        f"saved reports: {sorted(p.name for p in (tmp_path / 'reports').glob('*'))}"
    )
    return found[0].read_text(encoding="utf-8").splitlines()


def snapshot_reply(hosts: int = 2) -> str:
    return f"gpu_preflight: wrote {hosts * 80} counter lines for {hosts} of {hosts} hosts to /x/counters_snapshot.txt\n"


# The real diff prints a line per moved counter, then the summary lines, in this order.
MOVED = [
    f"nodeA mlx5_0 {ECN} +100",
    "nodeA mlx5_1 np_cnp_sent +5",
    "nodeA mlx5_0 np_cnp_sent +10",
    f"nodeB mlx5_0 {ECN} +7",
    "nodeB mlx5_2 rp_cnp_handled +3",
    "nodeA mlx5_0 port_xmit_data +5000",
    "nodeA mlx5_1 port_xmit_data +6000",
    "nodeB mlx5_0 port_xmit_data +7000",
]
ECN_YES = (
    "\n".join(
        [
            *MOVED,
            "ports with traffic: nodeA/mlx5_0 nodeA/mlx5_1 nodeB/mlx5_0",
            "ECN/CNP activity: yes",
            PROXY,
        ]
    )
    + "\n"
)


def diff_reply(activity: str, deltas: list[str] | None = None, ports: str = "none") -> str:
    lines = list(deltas or [])
    if not lines:
        lines.append("no counter moved")
    lines += [f"ports with traffic: {ports}", f"ECN/CNP activity: {activity}", PROXY]
    return "\n".join(lines) + "\n"


# --- snapshot ---------------------------------------------------------------------------------------------


def test_snapshot_ok(tmp_path: Path) -> None:
    result = call(tmp_path, ["snapshot", "nodeA", "nodeB"], out=snapshot_reply(2))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("diag-counters-snapshot | page 1/1")
    assert lines[1] == "VERDICT: OK snapshot of 2 hosts saved"
    assert stub_calls(tmp_path) == [f"counters snapshot {snapshot_file(tmp_path)} nodeA nodeB"]
    assert snapshot_file(tmp_path).is_file()
    assert re.search(r"^\[\d\d:\d\d:\d\d\] ", result.stderr, re.M)
    report = saved(tmp_path, "diag-counters-snapshot")
    assert report[0] == "VERDICT: OK snapshot of 2 hosts saved"
    assert any("wrote 160 counter lines for 2 of 2 hosts" in line for line in report)
    assert any("nplb diag counters diff" in line for line in report)


def test_snapshot_without_hosts_passes_none(tmp_path: Path) -> None:
    result = call(tmp_path, ["snapshot"], out=snapshot_reply(4))
    assert result.returncode == 0, result.stdout + result.stderr
    assert stub_calls(tmp_path) == [f"counters snapshot {snapshot_file(tmp_path)}"]
    assert "VERDICT: OK snapshot of 4 hosts saved" in result.stdout


def test_snapshot_creates_the_work_folder(tmp_path: Path) -> None:
    assert not (tmp_path / "work").exists()
    result = call(tmp_path, ["snapshot", "nodeA"], out=snapshot_reply(1))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERDICT: OK snapshot of 1 host saved" in result.stdout
    assert snapshot_file(tmp_path).is_file()


def test_snapshot_partial_is_warn_with_the_stubs_own_message(tmp_path: Path) -> None:
    out = "gpu_preflight: wrote 80 counter lines for 1 of 2 hosts to /x/counters_snapshot.txt\n"
    result = call(
        tmp_path, ["snapshot", "nodeA", "nodeB"], out=out, rc=1, err="nodeB ssh FAIL no route"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines()[1] == "VERDICT: WARN snapshot of 1 of 2 hosts"
    assert "nodeB ssh FAIL no route" in result.stderr
    report = saved(tmp_path, "diag-counters-snapshot")
    assert report[0] == "VERDICT: WARN snapshot of 1 of 2 hosts"
    assert "nodeB ssh FAIL no route" in report
    assert any("wrote 80 counter lines for 1 of 2 hosts" in line for line in report)


def test_snapshot_failure_of_the_stub_shows_its_error_and_no_report(tmp_path: Path) -> None:
    result = call(tmp_path, ["snapshot"], rc=2, err="gpu_preflight: cannot write /x")
    assert result.returncode == 2
    assert "gpu_preflight: cannot write /x" in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "reports").exists()


def test_missing_stub_is_a_usage_error(tmp_path: Path) -> None:
    gone = str(tmp_path / "nowhere" / "gpu_preflight.sh")
    for args in (["snapshot"], ["diff"]):
        write_snapshot(tmp_path, ["nodeA"])
        result = call(tmp_path, args, GPU_PREFLIGHT=gone)
        assert result.returncode == 2, result.stdout + result.stderr
        assert f"not found: {gone} (set GPU_PREFLIGHT)" in result.stderr
        assert result.stdout == ""


# --- diff -------------------------------------------------------------------------------------------------


def test_diff_ecn_moved(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    result = call(tmp_path, ["diff", "nodeA", "nodeB"], out=ECN_YES)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stub_calls(tmp_path) == [f"counters diff {snapshot_file(tmp_path)} nodeA nodeB"]
    screen = result.stdout.splitlines()
    assert screen[0].startswith("diag-counters-diff | page 1/2")
    assert (
        screen[1]
        == "VERDICT: OK ECN/CNP activity: yes (ECN/CNP moved on 2 of 2 hosts, 3 ports with traffic)"
    )
    page1 = "\n".join(screen)
    assert PROXY in page1
    assert re.search(
        r"^host\s+ECN marked\s+CNP sent\s+CNP handled\s+ports with traffic$", page1, re.M
    )
    assert re.search(r"^nodeA\s+100\s+15\s+0\s+2$", page1, re.M)
    assert re.search(r"^nodeB\s+7\s+0\s+3\s+1$", page1, re.M)
    assert "nodeA/mlx5_0 nodeA/mlx5_1 nodeB/mlx5_0" in page1
    assert f"nodeA mlx5_0 {ECN} +100" not in page1  # the raw lines start on page 2
    report = saved(tmp_path, "diag-counters-diff")
    assert report[0] == screen[1]
    for line in MOVED:
        assert line in report
    assert report.index(PROXY) < report.index(MOVED[0])


def test_diff_hosts_default_to_the_snapshot_hosts(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    result = call(tmp_path, ["diff"], out=ECN_YES)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stub_calls(tmp_path) == [f"counters diff {snapshot_file(tmp_path)}"]
    assert re.search(r"^nodeB\s+7\s+0\s+3\s+1$", result.stdout, re.M)


def test_diff_ecn_no(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    out = diff_reply("no", ["nodeA mlx5_0 port_xmit_data +5000"], ports="nodeA/mlx5_0")
    result = call(tmp_path, ["diff"], out=out)
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = result.stdout.splitlines()[1]
    assert verdict.startswith("VERDICT: OK ECN/CNP activity: no ")
    assert "2 hosts" in verdict and "1 port with traffic" in verdict
    assert re.search(r"^nodeA\s+0\s+0\s+0\s+1$", result.stdout, re.M)


def test_diff_ecn_unknown(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA"])
    out = diff_reply("unknown", ["nodeA mlx5_0 port_xmit_data +5000"], ports="nodeA/mlx5_0")
    result = call(tmp_path, ["diff"], out=out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        result.stdout.splitlines()[1]
        == "VERDICT: WARN ECN/CNP activity: unknown (no ECN counter could be read)"
    )
    assert re.search(r"^nodeA\s+-\s+-\s+-\s+1$", result.stdout, re.M)


def test_diff_nothing_moved(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    result = call(tmp_path, ["diff"], out=diff_reply("no"))
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = result.stdout.splitlines()[1]
    assert verdict.startswith("VERDICT: WARN no counter moved")
    assert "too fresh" in verdict and "did not run" in verdict
    assert "ports with traffic: none" in result.stdout
    assert PROXY in result.stdout
    assert not any(line == "\f" for line in saved(tmp_path, "diag-counters-diff"))


def test_diff_unreachable_host(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    out = (
        "\n".join(
            [
                f"nodeA mlx5_0 {ECN} +100",
                "ports with traffic: none",
                "ECN/CNP activity: yes",
                PROXY,
                "unreachable, not in this diff: nodeB",
            ]
        )
        + "\n"
    )
    result = call(
        tmp_path, ["diff", "nodeA", "nodeB"], out=out, rc=1, err="nodeB ssh FAIL no route"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    verdict = result.stdout.splitlines()[1]
    assert verdict.startswith("VERDICT: WARN ") and "nodeB" in verdict
    assert "nodeB ssh FAIL no route" in result.stderr
    report = saved(tmp_path, "diag-counters-diff")
    assert "unreachable, not in this diff: nodeB" in report
    assert "nodeB ssh FAIL no route" in report
    assert not any(re.match(r"^nodeB\s+\S+\s+\S+\s+\S+\s+\d+$", line) for line in report)
    assert any(re.match(r"^nodeA\s+100\s+0\s+0\s+0$", line) for line in report)


def test_diff_without_snapshot_is_a_usage_error(tmp_path: Path) -> None:
    result = call(tmp_path, ["diff"])
    assert result.returncode == 2
    assert "VERDICT: FAIL no snapshot: nplb diag counters snapshot" in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "reports").exists()
    assert stub_calls(tmp_path) == []


def test_diff_malformed_snapshot_shows_the_stubs_error(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA"])
    message = (
        f"gpu_preflight: {snapshot_file(tmp_path)} line 3 is not '<host> <dev> <name> <value>'"
    )
    result = call(tmp_path, ["diff"], rc=2, err=message)
    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "reports").exists()


def test_diff_unparsed_lines_are_kept(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA"])
    out = ECN_YES.replace("ECN/CNP activity: yes\n", "ECN/CNP activity: yes\nsomething new\n")
    result = call(tmp_path, ["diff"], out=out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "something new" in saved(tmp_path, "diag-counters-diff")


# --- the age of the snapshot ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "text"),
    [(45, "45s"), (125, "2m 05s"), (3725, "1h 02m"), (2 * 86400 + 3 * 3600, "2d 03h")],
)
def test_diff_shows_the_snapshot_age(tmp_path: Path, age: int, text: str) -> None:
    snap = write_snapshot(tmp_path, ["nodeA", "nodeB"])
    os.utime(snap, (1_000_000, 1_000_000))
    result = call(tmp_path, ["diff"], out=ECN_YES, DIAG_NOW=str(1_000_000 + age))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[2] == f"snapshot taken {text} ago"


def test_diff_snapshot_from_the_future(tmp_path: Path) -> None:
    snap = write_snapshot(tmp_path, ["nodeA", "nodeB"])
    os.utime(snap, (2_000_000, 2_000_000))
    result = call(tmp_path, ["diff"], out=ECN_YES, DIAG_NOW="1000000")
    assert "snapshot age unknown" in result.stdout.splitlines()[2]


def test_diff_age_uses_the_real_clock_without_diag_now(tmp_path: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    result = call(tmp_path, ["diff"], out=ECN_YES)
    assert re.match(r"^snapshot taken \d+s ago$", result.stdout.splitlines()[2]), result.stdout


# --- read only --------------------------------------------------------------------------------------------


def test_only_counter_subcommands_reach_the_stub(tmp_path: Path) -> None:
    call(tmp_path, ["snapshot", "nodeA", "nodeB"], out=snapshot_reply(2))
    call(tmp_path, ["diff", "nodeA", "nodeB"], out=ECN_YES)
    calls = stub_calls(tmp_path)
    assert len(calls) == 2
    assert calls[0].startswith("counters snapshot ")
    assert calls[1].startswith("counters diff ")
    written = sorted(p.name for p in (tmp_path / "work").iterdir())
    assert written == ["counters_snapshot.txt"]


@pytest.mark.parametrize("args", [[], ["bogus"], ["snapshot-not"]])
def test_script_usage(tmp_path: Path, args: list[str]) -> None:
    result = call(tmp_path, args)
    assert result.returncode == 2
    assert USAGE in result.stderr
    assert result.stdout == ""
    assert stub_calls(tmp_path) == []


# --- the dispatcher ---------------------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [
        NPLB / "nplb",
        NPLB / "lib" / "pages.sh",
        NPLB / "diag_counters.sh",
        *NPLB.glob("*.py"),
    ]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    return chk


def run_nplb(tmp_path: Path, chk: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=env_for(tmp_path),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_dispatcher_end_to_end(tmp_path: Path, checkout: Path) -> None:
    snap = call(tmp_path, ["snapshot", "nodeA", "nodeB"], out=snapshot_reply(2), checkout=checkout)
    assert snap.returncode == 0, snap.stdout + snap.stderr
    assert snap.stdout.splitlines()[1] == "VERDICT: OK snapshot of 2 hosts saved"
    assert snapshot_file(tmp_path).is_file()
    diff = call(tmp_path, ["diff", "nodeA", "nodeB"], out=ECN_YES, checkout=checkout)
    assert diff.returncode == 0, diff.stdout + diff.stderr
    assert diff.stdout.splitlines()[1].startswith("VERDICT: OK ECN/CNP activity: yes ")
    assert len(saved(tmp_path, "diag-counters-snapshot")) > 0
    assert len(saved(tmp_path, "diag-counters-diff")) > 0


def test_dispatcher_exit_codes_pass_through(tmp_path: Path, checkout: Path) -> None:
    write_snapshot(tmp_path, ["nodeA", "nodeB"])
    out = ECN_YES + "unreachable, not in this diff: nodeB\n"
    result = call(tmp_path, ["diff"], out=out, rc=1, checkout=checkout)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines()[1].startswith("VERDICT: WARN ")
    missing = call(
        tmp_path, ["snapshot"], rc=2, err="gpu_preflight: cannot write /x", checkout=checkout
    )
    assert missing.returncode == 2 and missing.stdout == ""


@pytest.mark.parametrize("args", [[], ["bogus"]])
def test_dispatcher_counters_usage(tmp_path: Path, checkout: Path, args: list[str]) -> None:
    result = call(tmp_path, args, checkout=checkout)
    assert result.returncode == 2
    assert USAGE in result.stderr
    assert result.stdout == ""


def test_dispatcher_diag_usage_names_both_verbs(tmp_path: Path, checkout: Path) -> None:
    result = run_nplb(tmp_path, checkout, "diag")
    assert result.returncode == 2
    assert (
        "usage: nplb diag run [TARGET] | nplb diag counters snapshot|diff [HOST...]"
        in result.stderr
    )
    assert result.stdout == ""


def test_help_lists_diag_counters(tmp_path: Path, checkout: Path) -> None:
    helped = run_nplb(tmp_path, checkout, "help")
    help_lines = helped.stdout.splitlines()
    assert any(line.startswith("  diag counters") for line in help_lines)
    assert any(line.startswith("  diag run") for line in help_lines)
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100


def test_every_page_fits_the_screen(tmp_path: Path, checkout: Path) -> None:
    hosts = [f"node1{n}0" for n in range(4)]
    names = [ECN, "np_cnp_sent", "rp_cnp_handled", "port_xmit_data", "port_rcv_data"]
    write_snapshot(tmp_path, hosts)
    raw: list[str] = []
    ports: list[str] = []
    for host in hosts:
        for dev in range(16):
            ports.append(f"{host}/mlx5_{dev}")
            raw += [f"{host} mlx5_{dev:<2} {name:<24} +{1234567 * (dev + 1)}" for name in names]
    out = (
        "\n".join([*raw, "ports with traffic: " + " ".join(ports), "ECN/CNP activity: yes", PROXY])
        + "\n"
    )
    first = call(tmp_path, ["diff", *hosts], out=out, checkout=checkout)
    assert first.returncode == 0, first.stdout + first.stderr
    match = re.match(r"^diag-counters-diff \| page 1/(\d+) ", first.stdout)
    assert match, first.stdout
    total = int(match.group(1))
    assert total >= 8
    page1 = first.stdout.splitlines()
    assert re.search(r"^node110\s+", first.stdout, re.M) and PROXY in first.stdout
    assert len(saved(tmp_path, "diag-counters-diff")) >= len(raw)
    for number in range(1, total + 1):
        shown = first if number == 1 else run_nplb(tmp_path, checkout, "page", str(number))
        lines = shown.stdout.splitlines() if number > 1 else page1
        assert 0 < len(lines) <= 38, (number, len(lines))
        assert max(len(line) for line in lines) <= 100, number
    last = run_nplb(tmp_path, checkout, "page", str(total))
    assert any(line.startswith("node130 mlx5_15") for line in last.stdout.splitlines())
