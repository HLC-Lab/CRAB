#!/usr/bin/env python3
"""The network state of an NPLB run: three independent switches, and the record every run carries.

    net_state.py declare nplb=on|off ar=on|off   write $WORK/net_state.json (what you say the switches are)
    net_state.py label nplb=0|1 ar=0|1 sr=0|1    print the state label, e.g. nplb1_ar0_sr1
    net_state.py sheet STATE | --restore          print the paste sheet of switch commands for a state
    net_state.py nic --hosts H,... [FILE]        parse the output of sr_state.sh (FILE or stdin) into JSON
    net_state.py show-report --nic JSON          print the report of `nplb net show`
    net_state.py declared                        exit 0 when a usable declaration exists, else 1
    net_state.py record DIR --nic JSON           write DIR/nplb_state.json

The three switches: NPLB (on/off) and AR = adaptive routing (on/off) are SWITCH settings you toggle by
hand on the switch CLI; SR = selective repeat is a per-node NIC setting (sr_set.sh), and the NIC has its
own adaptive-routing bit (ar_set.sh), reported as AR(nic). Nothing on the nodes can read the switches,
so net_state.json is a human claim and every report says so. The first `declare` also writes
net_state_initial.json, never overwritten, so `sheet --restore` can print the way back to the state at
slot start. STATE of `sheet` is `nplb=on ar=off` (also comma separated) or an alias: base (both off),
nplb, ar, nplb+ar. NET_SWITCHES (space separated names) is printed in the sheet header. The AR switch
command is `load-balance adaptive-orchestrator enable`, found on Leaf1 and not yet seen to change routing.

nplb_state.json: switch {nplb, ar, declared_at, together}, nic {sr, ar, hosts {host: {sr, ar}}}, label,
crab_rev and blink_rev ({hash, dirty}: dirty means tracked files are modified; blink_rev is null when the
Blink folder is absent) and recorded_at. `record` refuses (exit 1) when the declaration is missing or the
nodes disagree or cannot be read. WORK is $WORK, else $NPLB_WORK, else <checkout>/local/nplb-work.
CRAB_DIR defaults to this checkout; BLINK_DIR to ${NPLB_LAB:-$HOME/crab/lab}/blink-gpu. $NPLB_NOW
(YYYY-MM-DDTHH:MM:SS) replaces the clock, for tests. Exit 0 on success, 1 on a refusal or a FAIL report,
2 on bad input. Stdlib only, Python 3.10.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
ISO = "%Y-%m-%dT%H:%M:%S"
DECLARED_FILE = "net_state.json"
INITIAL_FILE = "net_state_initial.json"
RECORD_FILE = "nplb_state.json"
ONOFF = ("on", "off")
# the 8 network states of the campaign: (nplb, ar, sr), each 0 or 1
STATES = tuple((n, a, s) for n in (0, 1) for a in (0, 1) for s in (0, 1))
ALIASES = {
    "base": ("off", "off"),
    "nplb": ("on", "off"),
    "ar": ("off", "on"),
    "nplb+ar": ("on", "on"),
}
# From the Leaf1 session (Huawei CE switch): `undo mode2 enable` is refused while the self-heal id
# (id 1001 ...) is configured, so NPLB off removes the id first and NPLB on puts it back.
NPLB_ON_COMMANDS = (
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
)
NPLB_OFF_COMMANDS = (
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
)
NPLB_ON_NOTE = "note: if the id line says already configured, ignore it (it was never removed)"
NPLB_EVIDENCE = "NPLB commands: from the Leaf1 session screenshots; check one other switch with display this first"
# From the Leaf1 session (2026-11-12): `load-balance ?` in system view lists adaptive-orchestrator, whose
# only option is `enable`; `assign forward adaptive-routing-tuning enable` was already in the config.
AR_ON_COMMANDS = (
    "sys",
    "load-balance adaptive-orchestrator enable",
    "commit",
    "q",
    "display current-configuration | include adaptive",
)
AR_OFF_COMMANDS = (
    "sys",
    "undo load-balance adaptive-orchestrator enable",
    "commit",
    "q",
    "display current-configuration | include adaptive",
)
AR_EVIDENCE = "AR commands: found on Leaf1 with `load-balance ?`, not yet seen to change routing"
NO_SWITCHES = "set NET_SWITCHES in local/nplb_env.sh"
STANDING_NOTE = "switch state is as declared by you, it cannot be read from the nodes"
DECLARE_USAGE = "usage: nplb net declare nplb=on|off ar=on|off"
SHEET_USAGE = "usage: nplb net sheet nplb=on|off ar=on|off | base|nplb|ar|nplb+ar | --restore"
NOT_DECLARED = "no switch state declared: run nplb net declare"
TOGETHER_NOTE = "NPLB+AR together: untested on these switches, check the switch accepts it"
DEV_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+SR=(\S+)\s+AR=(\S+)(?:\s+(.*))?$")
FAIL_RE = re.compile(r"^(\S+)\s+FAIL\s+(.*)$")


class Fail(Exception):
    """A user-facing error: the message goes to stderr; CODE is the exit status (2 bad input, 1 refusal)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# --- pure helpers ---------------------------------------------------------------------------


def bit(value: object) -> int:
    """0 or 1 from 0/1, on/off, true/false (any case); ValueError otherwise."""
    text = str(value).strip().lower()
    if text in ("1", "on", "true"):
        return 1
    if text in ("0", "off", "false"):
        return 0
    raise ValueError(f"not 0/1 or on/off: {value!r}")


def label(nplb: object, ar: object, sr: object) -> str:
    """The label of one of the 8 states, e.g. nplb1_ar0_sr1. The campaign generator imports this."""
    return f"nplb{bit(nplb)}_ar{bit(ar)}_sr{bit(sr)}"


def switch_label(nplb: object, ar: object) -> str:
    return f"nplb{bit(nplb)}_ar{bit(ar)}"


def parse_pairs(
    tokens: list[str], keys: tuple[str, ...], usage: str, allowed: tuple[str, ...] = ONOFF
) -> dict[str, str]:
    """key=value tokens (also comma separated), each key exactly once; Fail on anything else."""
    words = [w for w in re.split(r"[,\s]+", " ".join(tokens)) if w]
    if not words:
        raise Fail(usage)
    found: dict[str, str] = {}
    for word in words:
        key, equals, value = word.partition("=")
        if not equals:
            raise Fail(f"'{word}' is not {key}=value ({usage})")
        if key not in keys:
            raise Fail(f"unknown key '{key}' (use {', '.join(keys)})")
        if key in found:
            raise Fail(f"{key}= given twice")
        if value not in allowed:
            raise Fail(f"{key}={value}: use {' or '.join(allowed)}")
        found[key] = value
    for key in keys:
        if key not in found:
            raise Fail(f"missing {key}={'|'.join(allowed)}")
    return found


def age_text(declared_at: str, now: datetime) -> str:
    """ "declared 12 min ago", or "age unknown" for an unparsable or future timestamp."""
    try:
        seconds = (now - datetime.fromisoformat(declared_at)).total_seconds()
    except (ValueError, TypeError):
        return "age unknown"
    if seconds < 0:
        return "age unknown"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "declared just now"
    if minutes < 120:
        return f"declared {minutes} min ago"
    if minutes < 48 * 60:
        return f"declared {minutes // 60} h ago"
    return f"declared {minutes // (24 * 60)} days ago"


def sheet_lines(nplb: str, ar: str, switches: str, restore_of: str = "") -> list[str]:
    """The paste sheet for the state nplb/ar (each "on" or "off")."""
    state = f"nplb={nplb} ar={ar}"
    lines = [f"VERDICT: sheet for {state}"]
    if restore_of:
        lines.append(f"restore initial state (declared {restore_of})")
    lines.append(f"switches: {switches or NO_SWITCHES}")
    lines.append("paste on every switch, one group after the other:")
    lines.append(NPLB_EVIDENCE)
    lines.append(f"NPLB {nplb}:")
    lines.extend(f"  {c}" for c in (NPLB_ON_COMMANDS if nplb == "on" else NPLB_OFF_COMMANDS))
    if nplb == "on":
        lines.append(NPLB_ON_NOTE)
    lines.append(f"AR {ar}:")
    lines.append(AR_EVIDENCE)
    lines.extend(f"  {c}" for c in (AR_ON_COMMANDS if ar == "on" else AR_OFF_COMMANDS))
    if nplb == "on" and ar == "on":
        lines.append("note: nplb+ar is the last state to try; if the switch refuses it, drop it")
    lines.append(f"then run: nplb net declare {state}")
    return lines


def uniform(values: Iterable[object]) -> int | None:
    """The one 0/1 value all of VALUES share, else None."""
    found = set(values)
    if len(found) == 1:
        (only,) = found
        if isinstance(only, int):
            return only
    return None


def odd_hosts(values: dict[str, object]) -> list[str]:
    """The hosts off the most common 0/1 value (a tie counts 0 as the common one); "mixed" hosts too."""
    ones = sum(1 for v in values.values() if v == 1)
    zeros = sum(1 for v in values.values() if v == 0)
    top = 0 if zeros >= ones else 1
    return [h for h, v in values.items() if v != top]


def is_mixed(values: Iterable[object]) -> bool:
    found = set(values)
    return "mixed" in found or len(found) > 1


def aggregate(values: list[str]) -> int | str:
    return int(values[0]) if len(set(values)) == 1 else "mixed"


def host_record(devices: list[tuple[str, str, str]], error: str) -> dict:
    """One host of the NIC JSON: sr and ar are 0, 1, "mixed" or None; error is "" when it was read."""
    record: dict = {"sr": None, "ar": None, "devices": len(devices), "error": error}
    if not error and not devices:
        record["error"] = "no device line from sr_state.sh"
    if record["error"]:
        return record
    bad = next((d for d in devices if d[0] not in ("0", "1")), None)
    if bad:
        record["error"] = f"SR not readable: {bad[2] or 'SR=?'}"
        return record
    record["sr"] = aggregate([d[0] for d in devices])
    if all(d[1] in ("0", "1") for d in devices):
        record["ar"] = aggregate([d[1] for d in devices])
    return record


def parse_sr_state(text: str, hosts: list[str]) -> dict[str, dict]:
    """host -> host_record from the lines of sr_state.sh ("<host> <dev> <pci> SR=x AR=y [msg]")."""
    devices: dict[str, list[tuple[str, str, str]]] = {h: [] for h in hosts}
    errors: dict[str, str] = {h: "" for h in hosts}
    for line in text.splitlines():
        failed = FAIL_RE.match(line)
        if failed and failed[1] in devices:
            errors[failed[1]] = errors[failed[1]] or failed[2]
            continue
        dev = DEV_RE.match(line)
        if dev and dev[1] in devices:
            devices[dev[1]].append((dev[4], dev[5], dev[6] or ""))
    return {h: host_record(devices[h], errors[h]) for h in hosts}


def summarize(hosts: dict[str, dict]) -> dict:
    """Problems (empty when all nodes agree and were read) and the common sr and ar values."""
    problems: list[str] = []
    unread = [h for h, v in hosts.items() if v["error"]]
    if unread:
        problems.append(f"{', '.join(unread)} unreadable")
    read = {h: v for h, v in hosts.items() if not v["error"]}
    sr_values = {h: v["sr"] for h, v in read.items()}
    ar_values = {h: v["ar"] for h, v in read.items() if v["ar"] is not None}
    blind = [h for h, v in read.items() if v["ar"] is None]
    if is_mixed(sr_values.values()):
        problems.append(f"SR mixed ({', '.join(odd_hosts(sr_values))})")
    if is_mixed(ar_values.values()):
        problems.append(f"AR(nic) mixed ({', '.join(odd_hosts(ar_values))})")
    if blind:
        problems.append(f"AR(nic) unreadable ({', '.join(blind)})")
    sr = uniform(sr_values.values()) if not unread else None
    ar = uniform(ar_values.values()) if not unread and not blind else None
    return {"problems": problems, "sr": sr, "ar": ar}


def show_lines(
    declared: dict | None, problem: str, hosts: dict[str, dict], now: datetime
) -> tuple[list[str], int]:
    """The `nplb net show` report and its exit status (1 on a FAIL)."""
    summary = summarize(hosts)
    code = 0
    if summary["problems"]:
        verdict = "VERDICT: FAIL " + "; ".join(summary["problems"])
        code = 1
    elif declared is None:
        what = (
            "no switch state declared" if problem == "missing" else f"{DECLARED_FILE} is {problem}"
        )
        verdict = f"VERDICT: WARN {what} (run: nplb net declare nplb=.. ar=..)"
    else:
        verdict = (
            f"VERDICT: OK nplb={declared['nplb']} ar={declared['ar']} "
            f"sr={summary['sr']} arnic={summary['ar']}"
        )
    if declared is None:
        shown = "declared: none"
    else:
        shown = f"declared: nplb={declared['nplb']} ar={declared['ar']}, {age_text(declared['declared_at'], now)}"
    lines = [verdict, shown, STANDING_NOTE, "NIC bits read just now with sr_state.sh:"]
    width = max([4, *(len(h) for h in hosts)])
    row = f"{{:<{width}}}  {{:<5}}  {{:<7}}  {{}}"
    lines.append(row.format("host", "SR", "AR(nic)", "devices"))
    for host, rec in hosts.items():
        cells = [("?" if rec[k] is None else str(rec[k])) for k in ("sr", "ar")]
        lines.append(row.format(host, cells[0], cells[1], rec["devices"]))
    lines.extend(f"{h}: {v['error']}" for h, v in hosts.items() if v["error"])
    return lines, code


# --- IO -------------------------------------------------------------------------------------


def work_dir() -> Path:
    explicit = os.environ.get("WORK") or os.environ.get("NPLB_WORK")
    return Path(explicit) if explicit else CHECKOUT / "local" / "nplb-work"


def now() -> datetime:
    fixed = os.environ.get("NPLB_NOW")
    if not fixed:
        return datetime.now().replace(microsecond=0)
    try:
        return datetime.strptime(fixed, ISO)
    except ValueError:
        raise Fail(f"NPLB_NOW={fixed!r} is not YYYY-MM-DDTHH:MM:SS") from None


def write_json(path: Path, data: dict) -> None:
    """Write through a temp file and a rename, so a reader never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def read_state(path: Path) -> tuple[dict | None, str]:
    """(state, "") or (None, why): why is "missing", "invalid" or "unreadable (...)"."""
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"unreadable ({exc})"
    ok = isinstance(data, dict) and data.get("nplb") in ONOFF and data.get("ar") in ONOFF
    if not ok or not isinstance(data.get("declared_at"), str):
        return None, "invalid"
    return data, ""


def require_declared() -> dict:
    declared, problem = read_state(work_dir() / DECLARED_FILE)
    if declared is None:
        if problem == "missing":
            raise Fail(NOT_DECLARED, 1)
        raise Fail(f"{DECLARED_FILE} is {problem}: run nplb net declare", 1)
    return declared


def read_nic(path: str) -> dict[str, dict]:
    try:
        hosts = json.loads(Path(path).read_text())["hosts"]
    except (OSError, ValueError, KeyError) as exc:
        raise Fail(f"cannot read the NIC state {path}: {exc}") from None
    return hosts


def git_rev(folder: Path) -> dict | None:
    """{hash, dirty} of a git folder (dirty: tracked files modified); None when the folder is absent."""
    if not folder.is_dir():
        return None
    unknown: dict = {"hash": "unknown", "dirty": None}
    try:
        head = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        status = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return unknown
    if head.returncode != 0:
        return unknown
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    return {"hash": head.stdout.strip(), "dirty": dirty}


# --- subcommands ----------------------------------------------------------------------------


def cmd_declare(args: argparse.Namespace) -> int:
    pairs = parse_pairs(args.state, ("nplb", "ar"), DECLARE_USAGE)
    state: dict = {
        "nplb": pairs["nplb"],
        "ar": pairs["ar"],
        "declared_at": now().isoformat(timespec="seconds"),
        "label_switch": switch_label(pairs["nplb"], pairs["ar"]),
    }
    together = pairs["nplb"] == "on" and pairs["ar"] == "on"
    if together:
        state["together"] = True
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    write_json(work / DECLARED_FILE, state)
    if not (work / INITIAL_FILE).exists():
        write_json(work / INITIAL_FILE, state)
    text = (
        f"declared: nplb={pairs['nplb']} ar={pairs['ar']} "
        "(switch state, as you told me; nothing here can read the switches)"
    )
    print(text + (f" ({TOGETHER_NOTE})" if together else ""))
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    usage = "usage: net_state.py label nplb=0|1 ar=0|1 sr=0|1"
    pairs = parse_pairs(args.state, ("nplb", "ar", "sr"), usage, ("0", "1", "on", "off"))
    print(label(pairs["nplb"], pairs["ar"], pairs["sr"]))
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    switches = " ".join(os.environ.get("NET_SWITCHES", "").split())
    if args.restore:
        if args.state:
            raise Fail("--restore takes no state")
        initial, problem = read_state(work_dir() / INITIAL_FILE)
        if initial is None:
            if problem == "missing":
                raise Fail("no initial state recorded (declare one first)")
            raise Fail(f"{INITIAL_FILE} is {problem}")
        lines = sheet_lines(initial["nplb"], initial["ar"], switches, initial["declared_at"])
    else:
        if len(args.state) == 1 and args.state[0] in ALIASES:
            nplb, ar = ALIASES[args.state[0]]
        else:
            pairs = parse_pairs(args.state, ("nplb", "ar"), SHEET_USAGE)
            nplb, ar = pairs["nplb"], pairs["ar"]
        lines = sheet_lines(nplb, ar, switches)
    print("\n".join(lines))
    return 0


def cmd_nic(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    hosts = [h for h in args.hosts.split(",") if h]
    print(json.dumps({"hosts": parse_sr_state(text, hosts)}, indent=2))
    return 0


def cmd_show_report(args: argparse.Namespace) -> int:
    declared, problem = read_state(work_dir() / DECLARED_FILE)
    lines, code = show_lines(declared, problem, read_nic(args.nic), now())
    print("\n".join(lines))
    return code


def cmd_declared(_args: argparse.Namespace) -> int:
    require_declared()
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    if not folder.is_dir():
        raise Fail(f"no such folder: {args.dir}")
    declared = require_declared()
    hosts = read_nic(args.nic)
    summary = summarize(hosts)
    if summary["problems"]:
        raise Fail("refusing to record: " + "; ".join(summary["problems"]), 1)
    nic_sr, nic_ar = summary["sr"], summary["ar"]
    record = {
        "switch": {
            "nplb": declared["nplb"],
            "ar": declared["ar"],
            "declared_at": declared["declared_at"],
            "together": declared["nplb"] == "on" and declared["ar"] == "on",
        },
        "nic": {
            "sr": nic_sr,
            "ar": nic_ar,
            "hosts": {h: {"sr": v["sr"], "ar": v["ar"]} for h, v in hosts.items()},
        },
        "label": label(declared["nplb"], declared["ar"], nic_sr),
        "crab_rev": git_rev(Path(os.environ.get("CRAB_DIR") or CHECKOUT)),
        "blink_rev": git_rev(blink_dir()),
        "recorded_at": now().isoformat(timespec="seconds"),
    }
    target = folder / RECORD_FILE
    write_json(target, record)
    print(f"recorded: {record['label']} -> {target}")
    return 0


def blink_dir() -> Path:
    explicit = os.environ.get("BLINK_DIR")
    if explicit:
        return Path(explicit)
    lab = os.environ.get("NPLB_LAB") or str(Path.home() / "crab" / "lab")
    return Path(lab) / "blink-gpu"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="net_state.py", description="NPLB network state")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("declare", "label"):
        sub.add_parser(name).add_argument("state", nargs="*")
    sheet = sub.add_parser("sheet")
    sheet.add_argument("--restore", action="store_true")
    sheet.add_argument("state", nargs="*")
    nic = sub.add_parser("nic")
    nic.add_argument("--hosts", required=True, help="comma separated, in report order")
    nic.add_argument("file", nargs="?")
    sub.add_parser("show-report").add_argument("--nic", required=True)
    sub.add_parser("declared")
    record = sub.add_parser("record")
    record.add_argument("dir")
    record.add_argument("--nic", required=True)
    return parser


COMMANDS = {
    "declare": cmd_declare,
    "label": cmd_label,
    "sheet": cmd_sheet,
    "nic": cmd_nic,
    "show-report": cmd_show_report,
    "declared": cmd_declared,
    "record": cmd_record,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except Fail as exc:
        print(f"net: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
