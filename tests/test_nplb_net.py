"""examples/nplb/net_tools.sh and net_state.py (`nplb net declare|set|show|sheet|record`).

Everything lives under tmp_path: fakes for sr_state.sh, sr_set.sh and ar_set.sh (env SR_STATE, SR_SET,
AR_SET) that log their arguments and print lines in the real formats, a fake sinfo, tiny real git repos
for the CRAB and Blink revisions, and a PATH that holds symlinks to the few real tools the scripts
need. No ssh, no network, no Slurm. The clock is NPLB_NOW."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
TOOLS = NPLB / "net_tools.sh"
STATE = NPLB / "net_state.py"
BASH = shutil.which("bash") or "/bin/bash"
NOW = "2026-11-10T09:30:00"
EARLIER = "2026-11-10T09:18:00"  # 12 minutes before NOW
HOSTS3 = ["node1", "node2", "node3"]
TOOL_NAMES = (
    "bash sh awk sed sort grep head tail cat date mkdir mv dirname basename uname python3 find tr "
    "cut wc ls stat env readlink mktemp touch chmod ln tee cp id sleep rm git timeout"
).split()
PLACEHOLDER = "AR: <fill in: command syntax not known yet>"
OFF_SEQUENCE = [
    "sys",
    "packet event monitor",
    "packet-loss self-heal",
    "undo id 1001 match nplb-er mode2 qos-local-id 13",
    "commit",
    "q",
    "q",
    "nplb-er mode mode2",
    "undo mode2 enable",
    "commit",
    "display this",
]
ON_SEQUENCE = [
    "sys",
    "nplb-er mode mode2",
    "mode2 enable",
    "commit",
    "packet event monitor",
    "packet-loss self-heal",
    "id 1001 match nplb-er mode2 qos-local-id 13",
    "commit",
    "q",
    "q",
    "display this",
]
ON_NOTE = "if the id line says already configured, ignore it (it was never removed)"
EVIDENCE = "NPLB commands: from the Leaf1 session screenshots; check one other switch with display this first"
AR_EVIDENCE = "AR commands: found on Leaf1 with `load-balance ?`, not yet seen to change routing"
AR_ON = [
    "sys",
    "load-balance adaptive-orchestrator enable",
    "commit",
    "q",
    "display current-configuration | include adaptive",
]
AR_OFF = ["sys", "undo load-balance adaptive-orchestrator enable", *AR_ON[2:]]
USAGE_VERBS = ("declare", "set", "show", "sheet", "record")

FAKE_SR_STATE = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_SR_STATE_LOG"
cat "$FAKE_SR_STATE_OUT"
exit "${FAKE_SR_STATE_RC:-0}"
"""
FAKE_SET = r"""#!/usr/bin/env bash
echo "$*" >>"$@LOG@"
value=$1
shift
for h in "$@"; do echo "$h mlx5_0 0000:3b:00.0 @LABEL@=$value OK"; done
if [[ ${@RC@:-0} != 0 ]]; then
    echo "@LABEL@=$value not set everywhere: 0 devices OK; failed: $*"
    exit "${@RC@}"
fi
echo "@LABEL@=$value on all $# devices of $# hosts"
"""
FAKE_SINFO = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_SINFO_LOG"
printf 'node1\nnode2\n'
"""


def make_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def sr_text(
    hosts: list[str], sr: int = 1, ar: int = 0, odd: dict[str, str] | None = None, ndev: int = 2
) -> str:
    """What sr_state.sh prints: a line per device (or a FAIL line), then a summary. ODD maps a host to
    its own text: "SR=0 AR=0" (every device), or "FAIL some message"."""
    odd = odd or {}
    lines = []
    for host in hosts:
        custom = odd.get(host, "")
        if custom.startswith("FAIL"):
            lines.append(f"{host:<10} {custom}")
            continue
        for dev in range(ndev):
            cells = custom or f"SR={sr} AR={ar}"
            lines.append(f"{host:<10} {'mlx5_' + str(dev):<8} {'0000:3b:0' + str(dev):<14} {cells}")
    lines.append(f"SR {sr} on all {len(hosts) * ndev} devices of {len(hosts)} hosts")
    return "\n".join(lines) + "\n"


def git(path: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(path), "GIT_CONFIG_GLOBAL": os.devnull}
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *cfg, *args], cwd=path, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def make_repo(path: Path) -> str:
    """A one-commit git repo; returns its short hash."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "file.txt").write_text("one\n")
    git(path, "add", "file.txt")
    git(path, "commit", "-q", "-m", "one")
    return git(path, "rev-parse", "--short", "HEAD")


@pytest.fixture
def site(tmp_path: Path) -> dict:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOL_NAMES:
        real = shutil.which(name)
        if real:
            (tools / name).symlink_to(real)
    fake = tmp_path / "fake"
    make_exe(fake / "sr_state.sh", FAKE_SR_STATE)
    make_exe(
        fake / "sr_set.sh",
        FAKE_SET.replace("@LABEL@", "SR")
        .replace("@LOG@", "FAKE_SR_SET_LOG")
        .replace("@RC@", "FAKE_SR_SET_RC"),
    )
    make_exe(
        fake / "ar_set.sh",
        FAKE_SET.replace("@LABEL@", "AR")
        .replace("@LOG@", "FAKE_AR_SET_LOG")
        .replace("@RC@", "FAKE_AR_SET_RC"),
    )
    make_exe(tmp_path / "sinfo_bin" / "sinfo", FAKE_SINFO)
    out = tmp_path / "sr_state.out"
    out.write_text(sr_text(HOSTS3))
    (tmp_path / "home").mkdir()
    crab_hash = make_repo(tmp_path / "crab_repo")
    blink_hash = make_repo(tmp_path / "blink_repo")
    return {
        "tmp": tmp_path,
        "tools": tools,
        "work": tmp_path / "work",
        "reports": tmp_path / "reports",
        "out": out,
        "crab": tmp_path / "crab_repo",
        "blink": tmp_path / "blink_repo",
        "crab_hash": crab_hash,
        "blink_hash": blink_hash,
        "logs": {
            "state": tmp_path / "sr_state.log",
            "sr": tmp_path / "sr_set.log",
            "ar": tmp_path / "ar_set.log",
            "sinfo": tmp_path / "sinfo.log",
        },
    }


def with_sinfo(site: dict) -> str:
    return f"{site['tmp'] / 'sinfo_bin'}:{site['tools']}"


def env_for(site: dict, **extra: str) -> dict:
    env = {
        "PATH": str(site["tools"]),
        "HOME": str(site["tmp"] / "home"),
        "WORK": str(site["work"]),
        "NPLB_REPORT_DIR": str(site["reports"]),
        "NPLB_NOW": NOW,
        "SR_STATE": str(site["tmp"] / "fake" / "sr_state.sh"),
        "SR_SET": str(site["tmp"] / "fake" / "sr_set.sh"),
        "AR_SET": str(site["tmp"] / "fake" / "ar_set.sh"),
        "CRAB_DIR": str(site["crab"]),
        "BLINK_DIR": str(site["blink"]),
        "FAKE_SR_STATE_OUT": str(site["out"]),
        "FAKE_SR_STATE_LOG": str(site["logs"]["state"]),
        "FAKE_SR_SET_LOG": str(site["logs"]["sr"]),
        "FAKE_AR_SET_LOG": str(site["logs"]["ar"]),
        "FAKE_SINFO_LOG": str(site["logs"]["sinfo"]),
        "PYTHONPATH": "/bad/pythonpath",
    }
    env.update(extra)
    return env


def run(site: dict, *args: str, script: Path = TOOLS, **env: str) -> subprocess.CompletedProcess:
    for log in site["logs"].values():
        log.touch()
    return subprocess.run(
        [BASH, str(script), *args],
        env=env_for(site, **env),
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def content(stdout: str) -> list[str]:
    """The report lines between the pager's header and footer."""
    return stdout.splitlines()[1:-1]


def declare(site: dict, state: str = "nplb=on ar=off", **env: str) -> subprocess.CompletedProcess:
    done = run(site, "declare", *state.split(), **env)
    assert done.returncode == 0, done.stderr
    return done


def table(lines: list[str]) -> dict[str, list[str]]:
    """host -> [SR, AR(nic), devices] from the table under the header `host SR AR(nic) devices`."""
    head = next(
        i for i, ln in enumerate(lines) if ln.split() == ["host", "SR", "AR(nic)", "devices"]
    )
    rows = {}
    for ln in lines[head + 1 :]:
        cells = ln.split()
        if len(cells) != 4 or not cells[0].startswith("node"):
            break
        rows[cells[0]] = cells[1:]
    return rows


def logged(site: dict, name: str) -> list[str]:
    return site["logs"][name].read_text().splitlines()


def load_module():
    spec = importlib.util.spec_from_file_location("net_state", STATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["net_state"] = module
    spec.loader.exec_module(module)
    return module


# ---- declare --------------------------------------------------------------------------------


def test_declare_writes_state_and_initial(site):
    first = declare(site, "nplb=on ar=off")
    assert first.returncode == 0
    state = site["work"] / "net_state.json"
    initial = site["work"] / "net_state_initial.json"
    expected = {"nplb": "on", "ar": "off", "declared_at": NOW, "label_switch": "nplb1_ar0"}
    assert json.loads(state.read_text()) == expected
    assert json.loads(initial.read_text()) == expected

    second = run(site, "declare", "nplb=off", "ar=off", NPLB_NOW="2026-11-10T10:00:00")
    assert second.returncode == 0, second.stderr
    assert second.stdout == (
        "declared: nplb=off ar=off (switch state, as you told me; nothing here can read the switches)\n"
    )
    now = json.loads(state.read_text())
    assert now == {
        "nplb": "off",
        "ar": "off",
        "declared_at": "2026-11-10T10:00:00",
        "label_switch": "nplb0_ar0",
    }
    assert json.loads(initial.read_text()) == expected  # never overwritten
    assert not list(site["work"].glob("*.tmp*"))


def test_declare_validates(site):
    cases = [
        ((), "usage"),
        (("nplb=on",), "ar"),
        (("ar=off",), "nplb"),
        (("nplb=maybe", "ar=off"), "maybe"),
        (("nplb=on", "ar=off", "speed=on"), "speed"),
        (("nplb=on", "ar=on", "ar=off"), "ar"),
        (("nplb", "ar=off"), "nplb"),
    ]
    for args, word in cases:
        bad = run(site, "declare", *args)
        assert bad.returncode == 2, args
        assert word in bad.stderr, (args, bad.stderr)
        assert bad.stdout == ""
        assert not (site["work"] / "net_state.json").exists(), args
        assert not (site["work"] / "net_state_initial.json").exists(), args


def test_declare_nplb_and_ar_together_is_flagged(site):
    done = declare(site, "nplb=on ar=on")
    assert done.stdout.splitlines() == [
        "declared: nplb=on ar=on (switch state, as you told me; nothing here can read the switches)"
        " (NPLB+AR together: untested on these switches, check the switch accepts it)"
    ]
    saved = json.loads((site["work"] / "net_state.json").read_text())
    assert saved == {
        "nplb": "on",
        "ar": "on",
        "declared_at": NOW,
        "label_switch": "nplb1_ar1",
        "together": True,
    }


def test_state_label(site):
    module = load_module()
    assert module.label(1, 0, 1) == "nplb1_ar0_sr1"
    assert module.label("on", "off", "on") == "nplb1_ar0_sr1"
    seen = set()
    for nplb in (0, 1):
        for ar in (0, 1):
            for sr in (0, 1):
                seen.add(module.label(nplb, ar, sr))
    assert len(seen) == 8
    assert module.label(0, 0, 0) == "nplb0_ar0_sr0"
    with pytest.raises(ValueError):
        module.label(2, 0, 0)
    with pytest.raises(ValueError):
        module.label("maybe", 0, 0)

    done = subprocess.run(
        [sys.executable, str(STATE), "label", "nplb=1", "ar=0", "sr=1"],
        env=env_for(site),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "nplb1_ar0_sr1\n"
    bad = subprocess.run(
        [sys.executable, str(STATE), "label", "nplb=1", "ar=0"],
        env=env_for(site),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bad.returncode == 2


# ---- set ------------------------------------------------------------------------------------


def test_set_calls_sr_and_ar_scripts_and_reports(site):
    done = run(site, "set", "sr=on", "arnic=off", "node1", "node2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert logged(site, "sr") == ["1 node1 node2"]
    assert logged(site, "ar") == ["0 node1 node2"]
    lines = content(done.stdout)
    assert lines[0] == "VERDICT: OK SR=1 AR(nic)=0 on 2 nodes"
    text = "\n".join(lines)
    assert "SR=1 on all 2 devices of 2 hosts" in text
    assert "AR=0 on all 2 devices of 2 hosts" in text

    run_off = run(site, "set", "sr=off", "node1", "node2")
    assert run_off.returncode == 0
    assert content(run_off.stdout)[0] == "VERDICT: OK SR=0 on 2 nodes"
    assert logged(site, "sr") == ["1 node1 node2", "0 node1 node2"]
    assert logged(site, "ar") == ["0 node1 node2"]  # not called without arnic=


def test_set_failure_names_the_bit(site):
    bad_ar = run(site, "set", "sr=on", "arnic=on", "node1", "node2", FAKE_AR_SET_RC="1")
    assert bad_ar.returncode == 1
    first = content(bad_ar.stdout)[0]
    assert first.startswith("VERDICT: FAIL ")
    assert "AR(nic)" in first and "SR" not in first
    bad_sr = run(site, "set", "sr=on", "node1", "node2", FAKE_SR_SET_RC="1")
    assert bad_sr.returncode == 1
    assert content(bad_sr.stdout)[0].startswith("VERDICT: FAIL SR")
    both = run(site, "set", "sr=on", "arnic=on", "node1", FAKE_SR_SET_RC="1", FAKE_AR_SET_RC="1")
    assert both.returncode == 1
    assert "SR" in content(both.stdout)[0] and "AR(nic)" in content(both.stdout)[0]
    assert logged(site, "ar")[-1] == "1 node1"  # the AR bit is still tried after SR failed


def test_set_bad_usage_calls_nothing(site):
    cases = [
        ("sr=maybe", "node1"),
        ("sr=on", "arnic=2", "node1"),
        ("arnic=on", "node1"),
        ("sr=on", "sr=off", "node1"),
        ("sr=on", "--bogus"),
        ("speed=on", "sr=on", "node1"),
        (),
    ]
    for args in cases:
        bad = run(site, "set", *args)
        assert bad.returncode == 2, args
        assert bad.stderr.strip(), args
        assert bad.stdout == ""
        assert logged(site, "sr") == [] and logged(site, "ar") == [], args


def test_set_without_hosts_needs_sinfo(site):
    none = run(site, "set", "sr=on")
    assert none.returncode == 2
    assert "sinfo" in none.stderr and "HOST" in none.stderr
    assert logged(site, "sr") == []
    listed = run(site, "set", "sr=on", PATH=with_sinfo(site), PARTITION="part_x")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert logged(site, "sinfo") == ["-h -N -p part_x -o %N"]
    assert logged(site, "sr") == ["1 node1 node2"]


# ---- show -----------------------------------------------------------------------------------


def test_show_reports_declared_and_live_state(site):
    declare(site, "nplb=on ar=off", NPLB_NOW=EARLIER)
    done = run(site, "show", *HOSTS3)
    assert done.returncode == 0, done.stdout + done.stderr
    assert logged(site, "state") == ["node1 node2 node3"]
    lines = content(done.stdout)
    assert lines[0] == "VERDICT: OK nplb=on ar=off sr=1 arnic=0"
    assert table(lines) == {h: ["1", "0", "2"] for h in HOSTS3}
    text = "\n".join(lines)
    assert "declared 12 min ago" in text
    assert "switch state is as declared by you, it cannot be read from the nodes" in text

    unknown_future = run(site, "show", *HOSTS3, NPLB_NOW="2026-11-10T09:00:00")
    assert "age unknown" in unknown_future.stdout
    assert "declared 12 min ago" not in unknown_future.stdout

    (site["work"] / "net_state.json").write_text(
        json.dumps(
            {"nplb": "on", "ar": "off", "declared_at": "garbage", "label_switch": "nplb1_ar0"}
        )
    )
    garbage = run(site, "show", *HOSTS3)
    assert garbage.returncode == 0
    assert "age unknown" in garbage.stdout

    listed = run(site, "show", PATH=with_sinfo(site))
    assert listed.returncode == 0, listed.stderr
    assert logged(site, "state")[-1] == "node1 node2"


def test_show_mixed_sr_is_a_fail(site):
    declare(site)
    site["out"].write_text(sr_text(HOSTS3, odd={"node2": "SR=0 AR=0"}))
    mixed = run(site, "show", *HOSTS3)
    assert mixed.returncode == 1
    lines = content(mixed.stdout)
    assert lines[0] == "VERDICT: FAIL SR mixed (node2)"
    assert table(lines)["node2"] == ["0", "0", "2"]

    site["out"].write_text(sr_text(HOSTS3, odd={"node2": "SR=1 AR=1"}))
    mixed_ar = run(site, "show", *HOSTS3)
    assert mixed_ar.returncode == 1
    assert content(mixed_ar.stdout)[0] == "VERDICT: FAIL AR(nic) mixed (node2)"

    site["out"].write_text(sr_text(HOSTS3, odd={"node3": "FAIL ssh exit 255: no route"}))
    unread = run(site, "show", *HOSTS3)
    assert unread.returncode == 1
    first = content(unread.stdout)[0]
    assert first.startswith("VERDICT: FAIL ") and "node3" in first
    assert "ssh exit 255: no route" in unread.stdout

    site["out"].write_text("")
    silent = run(site, "show", *HOSTS3)
    assert silent.returncode == 1
    assert content(silent.stdout)[0].startswith("VERDICT: FAIL ")


def test_show_without_declaration_is_a_warn(site):
    done = run(site, "show", *HOSTS3)
    assert done.returncode == 0
    assert content(done.stdout)[0] == (
        "VERDICT: WARN no switch state declared (run: nplb net declare nplb=.. ar=..)"
    )
    assert table(content(done.stdout)) == {h: ["1", "0", "2"] for h in HOSTS3}


# ---- sheet ----------------------------------------------------------------------------------


def stripped(lines: list[str]) -> list[str]:
    return [ln.strip() for ln in lines]


def in_order(lines: list[str], wanted: list[str]) -> bool:
    it = iter(stripped(lines))
    return all(any(w == ln for ln in it) for w in wanted)


def test_sheet_for_each_state(site):
    on_cmds = ON_SEQUENCE
    off_cmds = OFF_SEQUENCE
    spellings = [
        (["nplb=on,ar=off"], "nplb=on ar=off"),
        (["nplb=on", "ar=off"], "nplb=on ar=off"),
        (["nplb"], "nplb=on ar=off"),
        (["base"], "nplb=off ar=off"),
        (["ar"], "nplb=off ar=on"),
        (["nplb+ar"], "nplb=on ar=on"),
        (["nplb=off,ar=on"], "nplb=off ar=on"),
    ]
    for args, state in spellings:
        done = run(site, "sheet", *args, NET_SWITCHES="swA swB")
        assert done.returncode == 0, (args, done.stderr)
        lines = content(done.stdout)
        assert lines[0] == f"VERDICT: sheet for {state}", args
        assert "switches: swA swB" in lines
        assert PLACEHOLDER not in stripped(lines)
        assert in_order(lines, AR_ON if "ar=on" in state else AR_OFF), args
        assert EVIDENCE in stripped(lines), args
        assert lines[-1] == f"then run: nplb net declare {state}"
        if "nplb=on" in state:
            assert in_order(lines, on_cmds), args
            assert ON_NOTE in " ".join(stripped(lines)), args
        else:
            assert in_order(lines, off_cmds), args
            assert "mode2 enable" not in stripped(lines), args
            assert ON_NOTE not in " ".join(stripped(lines)), args
        assert 0 < len(done.stdout.splitlines()) <= 38
        assert max(len(ln) for ln in done.stdout.splitlines()) <= 100

    hint = run(site, "sheet", "base")
    assert "switches: set NET_SWITCHES in local/nplb_env.sh" in content(hint.stdout)
    last = run(site, "sheet", "nplb+ar")
    assert "last state to try" in last.stdout
    other = run(site, "sheet", "nplb")
    assert "last state to try" not in other.stdout


def test_sheet_bad_usage(site):
    for args in ((), ("frob",), ("nplb=on",), ("--restore", "nplb"), ("nplb=on", "ar=maybe")):
        bad = run(site, "sheet", *args)
        assert bad.returncode == 2, args
        assert bad.stdout == ""
        assert bad.stderr.strip()


def test_sheet_restore_uses_the_initial_state(site):
    none = run(site, "sheet", "--restore")
    assert none.returncode == 2
    assert "no initial state recorded (declare one first)" in none.stderr
    assert none.stdout == ""

    declare(site, "nplb=on ar=off", NPLB_NOW=EARLIER)
    declare(site, "nplb=off ar=off")
    done = run(site, "sheet", "--restore")
    assert done.returncode == 0, done.stderr
    lines = content(done.stdout)
    assert lines[0] == "VERDICT: sheet for nplb=on ar=off"
    assert any(ln.startswith("restore initial state") for ln in lines)
    assert in_order(lines, ON_SEQUENCE)
    assert ON_NOTE in " ".join(stripped(lines))
    assert lines[-1] == "then run: nplb net declare nplb=on ar=off"


def test_sheet_off_removes_the_self_heal_id_before_the_undo(site):
    done = run(site, "sheet", "base")
    assert done.returncode == 0, done.stderr
    lines = stripped(content(done.stdout))
    undo_id = lines.index("undo id 1001 match nplb-er mode2 qos-local-id 13")
    undo_mode = lines.index("undo mode2 enable")
    assert undo_id < undo_mode
    assert "commit" in lines[undo_id + 1 : undo_mode]
    assert in_order(lines, OFF_SEQUENCE)


def test_sheet_on_restores_the_self_heal_id(site):
    done = run(site, "sheet", "nplb")
    assert done.returncode == 0, done.stderr
    lines = stripped(content(done.stdout))
    assert in_order(lines, ON_SEQUENCE)
    assert any(ln.endswith(ON_NOTE) for ln in lines)
    assert "undo id 1001 match nplb-er mode2 qos-local-id 13" not in lines


def test_sheet_evidence_is_at_most_two_lines(site):
    done = run(site, "sheet", "base")
    lines = stripped(content(done.stdout))
    at = [i for i, ln in enumerate(lines) if "NPLB commands:" in ln or "Leaf1" in ln]
    assert at, lines
    assert len(at) <= 2
    assert EVIDENCE in lines


def test_sheet_ar_prints_the_on_and_off_lines_with_their_evidence(site):
    for args, expected in (
        (["base"], AR_OFF),
        (["ar"], AR_ON),
        (["nplb+ar"], AR_ON),
        (["nplb"], AR_OFF),
    ):
        done = run(site, "sheet", *args)
        lines = stripped(content(done.stdout))
        assert AR_EVIDENCE in lines, args
        at = lines.index(AR_EVIDENCE)
        assert lines[at + 1 : at + 1 + len(expected)] == expected, args
        assert PLACEHOLDER not in lines, args


# ---- record ---------------------------------------------------------------------------------


def test_record_writes_the_run_state(site):
    declare(site, "nplb=on ar=off", NPLB_NOW=EARLIER)
    run_dir = site["tmp"] / "run1"
    run_dir.mkdir()
    done = run(site, "record", str(run_dir), *HOSTS3)
    assert done.returncode == 0, done.stdout + done.stderr
    target = run_dir / "nplb_state.json"
    assert done.stdout == f"recorded: nplb1_ar0_sr1 -> {target}\n"
    data = json.loads(target.read_text())
    assert set(data) == {"switch", "nic", "label", "crab_rev", "blink_rev", "recorded_at"}
    assert data["switch"] == {"nplb": "on", "ar": "off", "declared_at": EARLIER, "together": False}
    assert data["nic"] == {"sr": 1, "ar": 0, "hosts": {h: {"sr": 1, "ar": 0} for h in HOSTS3}}
    assert data["label"] == "nplb1_ar0_sr1"
    assert data["crab_rev"] == {"hash": site["crab_hash"], "dirty": False}
    assert data["blink_rev"] == {"hash": site["blink_hash"], "dirty": False}
    assert data["recorded_at"] == NOW

    (site["crab"] / "file.txt").write_text("changed\n")
    shutil.rmtree(site["blink"])
    again = run(site, "record", str(run_dir), *HOSTS3)
    assert again.returncode == 0, again.stderr
    data = json.loads(target.read_text())
    assert data["crab_rev"] == {"hash": site["crab_hash"], "dirty": True}
    assert data["blink_rev"] is None


def test_record_refuses(site):
    run_dir = site["tmp"] / "run1"
    run_dir.mkdir()
    target = run_dir / "nplb_state.json"

    nothing = run(site, "record", str(run_dir), *HOSTS3)
    assert nothing.returncode == 1
    assert "no switch state declared: run nplb net declare" in nothing.stderr
    assert not target.exists()

    declare(site)
    site["out"].write_text(sr_text(HOSTS3, odd={"node2": "SR=0 AR=0"}))
    mixed = run(site, "record", str(run_dir), *HOSTS3)
    assert mixed.returncode == 1
    assert "SR mixed (node2)" in mixed.stderr
    assert not target.exists()

    site["out"].write_text(sr_text(HOSTS3, odd={"node3": "FAIL ssh exit 255: no route"}))
    unread = run(site, "record", str(run_dir), *HOSTS3)
    assert unread.returncode == 1
    assert "node3" in unread.stderr
    assert not target.exists()

    site["out"].write_text(sr_text(HOSTS3))
    missing_dir = run(site, "record", str(site["tmp"] / "nope"), *HOSTS3)
    assert missing_dir.returncode == 2
    assert "nope" in missing_dir.stderr
    no_args = run(site, "record")
    assert no_args.returncode == 2


# ---- paging and narration -------------------------------------------------------------------


def test_net_report_pages_fit(site):
    declare(site, "nplb=on ar=on", NPLB_NOW=EARLIER)
    many = [f"node{i}" for i in range(1, 9)]
    site["out"].write_text(sr_text(many, odd={"node4": "SR=0 AR=1"}))
    outputs = [
        run(site, "show", *many),
        run(site, "set", "sr=on", "arnic=on", *many),
        run(site, "sheet", "nplb+ar"),
        run(site, "sheet", "--restore"),
    ]
    for done in outputs:
        lines = done.stdout.splitlines()
        assert 0 < len(lines) <= 38, done.stdout
        assert max(len(ln) for ln in lines) <= 100, done.stdout
        assert lines[0].split(" | ")[1].startswith("page 1/")


def test_narrates_live_on_stderr(site):
    declare(site)
    stamp = r"^\[\d\d:\d\d:\d\d\] "
    shown = run(site, "show", *HOSTS3)
    notes = [ln for ln in shown.stderr.splitlines() if ln.strip()]
    assert notes and all(re.match(stamp, ln) for ln in notes), shown.stderr
    assert any("reading" in ln for ln in notes)
    assert shown.stdout.splitlines()[0].startswith("net-show | page 1/1")

    setting = run(site, "set", "sr=on", "arnic=off", "node1", "node2")
    notes = [ln for ln in setting.stderr.splitlines() if ln.strip()]
    assert notes and all(re.match(stamp, ln) for ln in notes), setting.stderr
    assert any("SR=1" in ln for ln in notes)
    assert any("AR(nic)=0" in ln for ln in notes)
    assert setting.stdout.splitlines()[0].startswith("net-set | page 1/1")


# ---- dispatcher -----------------------------------------------------------------------------


@pytest.fixture
def checkout(site) -> Path:
    chk = site["tmp"] / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for rel in ("nplb", "net_tools.sh", "net_state.py", "pages.py", "lib/pages.sh"):
        shutil.copy(NPLB / rel, nplb_dir / rel)
    return chk


def test_dispatcher_net_verbs(site, checkout):
    nplb = checkout / "examples" / "nplb" / "nplb"
    done = run(site, "net", "declare", "nplb=off", "ar=on", script=nplb)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("declared: nplb=off ar=on")
    shown = run(site, "net", "show", *HOSTS3, script=nplb)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert content(shown.stdout)[0] == "VERDICT: OK nplb=off ar=on sr=1 arnic=0"
    sheet = run(site, "net", "sheet", "base", script=nplb)
    assert content(sheet.stdout)[0] == "VERDICT: sheet for nplb=off ar=off"
    setting = run(site, "net", "set", "sr=off", "node1", script=nplb)
    assert setting.returncode == 0
    assert logged(site, "sr") == ["0 node1"]
    run_dir = site["tmp"] / "r"
    run_dir.mkdir()
    rec = run(site, "net", "record", str(run_dir), *HOSTS3, script=nplb)
    assert rec.returncode == 0, rec.stderr
    assert (run_dir / "nplb_state.json").is_file()

    for args in ((), ("frob",)):
        bad = run(site, "net", *args, script=nplb)
        assert bad.returncode == 2, args
        for verb in USAGE_VERBS:
            assert verb in bad.stderr, (args, verb)
        assert "usage: nplb net" in bad.stderr

    helped = run(site, "help", script=nplb)
    assert helped.returncode == 0
    lines = helped.stdout.splitlines()
    assert any(ln.startswith("  net ") for ln in lines)
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
