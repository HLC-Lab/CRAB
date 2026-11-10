"""`nplb run`, `nplb cancel`, `nplb skip` (examples/nplb/run_cmd.py) and the SKIPPED status of run_log.py.

The module runs as a subprocess in a fake checkout under `tmp_path` (so a relative data dir of `crab
run` lands there), with fake `crab`, `squeue`, `scancel`, `scontrol` and `net_tools.sh` scripts that
log their arguments and obey small control files in `$FAKE_DIR`. Nothing starts a Slurm job or sends
a signal; `scancel` is a fake that only logs the job id it was given.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SCHEDULE_HEADER = (
    "n\ttier\tstate\tnplb\tar\tsr\tblock\tlayout\tvictim\test_min\tswitch_change\tsr_change"
    "\tcum_h\tfile"
)
RUNS_HEADER = "n\tstate\tblock\tlayout\tvictim\tjob\tdata_dir\tsubmitted\tstatus\telapsed_s\tnote"
NOW = "2026-11-10T12:00:00"
B0, B1 = "nplb0_ar0_sr0", "nplb1_ar0_sr1"
SIX = [
    ("1", B0, "a2a", 5.0),
    ("1", B0, "agtr", 6.0),
    ("1", B0, "ardc", 4.0),
    ("2", B1, "a2a", 5.3),
    ("2", B1, "agtr", 6.0),
    ("2", B1, "ardc", 3.5),
]


def _load(name: str) -> ModuleType:
    """Import a script from examples/nplb (not a package) under its own file name."""
    if str(NPLB) not in sys.path:
        sys.path.insert(0, str(NPLB))
    spec = importlib.util.spec_from_file_location(name, NPLB / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run_log = _load("run_log")
run_cmd = _load("run_cmd")

FAKE_CRAB = r"""#!/usr/bin/env bash
d=$FAKE_DIR
echo "$(pwd -P)|$*" >>"$d/crab_calls"
case $1 in
validate)
    if [[ -f $d/validate_fail ]]; then cat "$d/validate_fail"; exit 1; fi
    exit 0
    ;;
run)
    block=${@: -1}
    mode=$(cat "$d/run_mode" 2>/dev/null || echo relative)
    if [[ $mode == fail ]]; then
        printf 'line1\nline2\nline3\nline4\nline5\nline6\nboom: no allocation\n' >&2
        exit 1
    fi
    if [[ $mode == nojob ]]; then echo '{"hello": 1}'; exit 0; fi
    job=$(cat "$d/job_id" 2>/dev/null || echo 4711)
    name=$(basename "$block" .json)_2026-11-10_12-00-00
    rel=data/nplb_h100_gpu/$name
    mkdir -p "$rel"
    printf 'Worker started\nEngine run finished  elapsed=0:02:00\n' >"$rel/slurm_output.log"
    if [[ $mode == absolute ]]; then rel=$(pwd -P)/$rel; fi
    printf '{"job_id": "%s", "data_dir": "%s"}\n' "$job" "$rel"
    ;;
esac
"""

FAKE_SQUEUE = r"""#!/usr/bin/env bash
echo "squeue $*" >>"$FAKE_DIR/squeue_calls"
case " $* " in
*" -j "*) exit 0 ;;
esac
[[ -f $FAKE_DIR/partition_jobs ]] && cat "$FAKE_DIR/partition_jobs"
exit 0
"""

FAKE_SCONTROL = r"""#!/usr/bin/env bash
echo "slurm_load_jobs error: Invalid job id specified" >&2
exit 1
"""

FAKE_SCANCEL = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_DIR/scancel_calls"
rc=$(cat "$FAKE_DIR/scancel_rc" 2>/dev/null || echo 0)
if [[ $rc != 0 ]]; then
    echo "scancel: error: Invalid job id $1" >&2
    echo "second error line" >&2
fi
exit "$rc"
"""

FAKE_NET_TOOLS = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_DIR/net_calls"
if [[ -f $FAKE_DIR/net_fail ]]; then cat "$FAKE_DIR/net_fail"; exit 1; fi
sr=$(cat "$FAKE_DIR/nic_sr" 2>/dev/null || echo 0)
cat >"$2/nplb_state.json" <<JSON
{"switch": {"nplb": "off", "ar": "off"}, "nic": {"sr": $sr}, "label": "state-sr$sr"}
JSON
echo "recorded: state -> $2/nplb_state.json"
"""


def schedule_text(specs: list[tuple[str, str, str, float]]) -> str:
    lines = [SCHEDULE_HEADER]
    cum = 0.0
    for n, (tier, state, victim, est) in enumerate(specs, 1):
        cum += est
        bits = ["-"] * 3 if state == "-" else list(re.findall(r"\d", state))
        cells = [str(n), tier, state, *bits, "t1", "split-i", victim, f"{est:.1f}", "0", "0"]
        cells += [f"{cum / 60:.2f}", f"t1_split-i_{victim}.json"]
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def run_line(
    n: int, status: str, job: str | None = None, elapsed: int | str = "", note: str = ""
) -> str:
    cells = [str(n), B0, "t1", "split-i", "a2a", job or str(1000 + n), f"/data/{n}"]
    cells += ["2026-11-10T11:00:00", status, str(elapsed), note]
    return "\t".join(cells)


class World:
    """A fake checkout, a lab with fake commands, and the campaign files, all under tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.chk = tmp_path.resolve() / "chk"
        nplb_dir = self.chk / "examples" / "nplb"
        (nplb_dir / "lib").mkdir(parents=True)
        for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
            shutil.copy(path, nplb_dir / path.relative_to(NPLB))
        (nplb_dir / "nplb").chmod(0o755)
        self.fake = tmp_path / "fake"
        self.fake.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        scripts = {
            "crab": FAKE_CRAB,
            "squeue": FAKE_SQUEUE,
            "scontrol": FAKE_SCONTROL,
            "scancel": FAKE_SCANCEL,
            "net_tools.sh": FAKE_NET_TOOLS,
        }
        for name, body in scripts.items():
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        (tmp_path / "work").mkdir()
        (tmp_path / "camp").mkdir()
        (tmp_path / "elsewhere").mkdir()
        self.put_schedule(SIX)
        self.declare("off", "off")

    @property
    def runs_file(self) -> Path:
        return self.root / "work" / "campaign_runs.tsv"

    def put_schedule(self, specs: list[tuple[str, str, str, float]]) -> None:
        camp = self.root / "camp"
        (camp / "schedule.tsv").write_text(schedule_text(specs), encoding="utf-8")
        for _tier, _state, victim, _est in specs:
            (camp / f"t1_split-i_{victim}.json").write_text("{}", encoding="utf-8")

    def declare(self, nplb: str, ar: str) -> None:
        state = {"nplb": nplb, "ar": ar, "declared_at": "2026-11-10T11:48:00"}
        (self.root / "work" / "net_state.json").write_text(json.dumps(state), encoding="utf-8")

    def put_runs(self, *lines: str) -> None:
        self.runs_file.write_text("\n".join([RUNS_HEADER, *lines]) + "\n", encoding="utf-8")

    def control(self, name: str, text: str) -> None:
        (self.fake / name).write_text(text, encoding="utf-8")

    def calls(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def submits(self) -> list[str]:
        return [c for c in self.calls("crab_calls") if "|run " in c]

    def runs(self) -> list[object]:
        return run_log.read_runs(self.runs_file)

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        for var in ("PYTHONPATH", "NPLB_WORK", "PAGE_LINES", "PAGE_COLS", "PRESET", "PARTITION"):
            env.pop(var, None)
        env.pop("NPLB_YES", None)
        env.update(
            FAKE_DIR=str(self.fake),
            CRAB=str(self.bin / "crab"),
            SQUEUE=str(self.bin / "squeue"),
            SCONTROL=str(self.bin / "scontrol"),
            SCANCEL=str(self.bin / "scancel"),
            NET_TOOLS=str(self.bin / "net_tools.sh"),
            WORK=str(self.root / "work"),
            CAMPAIGN_DIR=str(self.root / "camp"),
            NPLB_REPORT_DIR=str(self.root / "reports"),
            NPLB_NOW=NOW,
            FOLLOW_POLL="0.05",
            FOLLOW_STALL="1",
        )
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env.update(extra)
        return env

    def py(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        script = self.chk / "examples" / "nplb" / "run_cmd.py"
        return subprocess.run(
            [sys.executable, str(script), *args],
            env=self.env(**env),
            cwd=self.root / "elsewhere",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def nplb(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.chk / "examples" / "nplb" / "nplb"), *args],
            env=self.env(**env),
            cwd=self.root / "elsewhere",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def stamped(stderr: str) -> list[str]:
    """The narration lines without their `[HH:MM:SS] ` stamp; every line must carry one."""
    texts = []
    for line in stderr.splitlines():
        found = re.match(r"^\[(\d\d:\d\d:\d\d)\] (.*)$", line)
        assert found, f"narration line without a stamp: {line!r}"
        texts.append(found.group(2))
    return texts


# --- run: the happy path -----------------------------------------------------------------------------


def test_run_happy_path_no_follow(world: World) -> None:
    result = world.py("run", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[-1] == "job 4711"
    data_dir = world.chk / "data" / "nplb_h100_gpu" / "t1_split-i_a2a_2026-11-10_12-00-00"
    assert lines[-2] == f"submitted: row 1 job 4711 -> {data_dir}"
    block = world.root / "camp" / "t1_split-i_a2a.json"
    assert world.submits() == [f"{world.chk}|run -p nplb_h100_gpu --json {block}"]
    assert any(c.endswith(f"validate -p nplb_h100_gpu {block}") for c in world.calls("crab_calls"))
    state = json.loads((data_dir / "nplb_state.json").read_text(encoding="utf-8"))
    assert state["nic"]["sr"] == 0
    row = json.loads((data_dir / "schedule_row.json").read_text(encoding="utf-8"))
    assert (row["n"], row["state"], row["victim"], row["file"]) == (
        1,
        B0,
        "a2a",
        "t1_split-i_a2a.json",
    )
    (only,) = world.runs()
    assert (only.n, only.state, only.block, only.layout, only.victim) == (
        1,
        B0,
        "t1",
        "split-i",
        "a2a",
    )
    assert (only.job, only.data_dir, only.submitted) == ("4711", str(data_dir), NOW)
    assert (only.status, only.elapsed_s, only.note) == ("SUBMITTED", None, "")
    assert world.calls("net_calls")[0].startswith("record " + str(world.root / "work" / "staging"))
    assert stamped(result.stderr)  # narration exists and every line is stamped


# --- run: the gates ----------------------------------------------------------------------------------


def break_running(w: World) -> None:
    w.put_runs(run_line(2, "SUBMITTED", job="4242"))


def break_block(w: World) -> None:
    (w.root / "camp" / "t1_split-i_a2a.json").unlink()


def break_done(w: World) -> None:
    w.put_runs(run_line(1, "OK", elapsed=300))


def break_declared(w: World) -> None:
    w.declare("on", "off")


def break_net_tools(w: World) -> None:
    w.control("net_fail", "no switch state declared: run nplb net declare\n")


def break_validate(w: World) -> None:
    w.control("validate_fail", "".join(f"validate line {i}\n" for i in range(1, 8)))


def break_partition(w: World) -> None:
    w.control("partition_jobs", "4801\n4802\n")


GATES = [
    (
        "running",
        break_running,
        "job 4242 of row 2 is still running: nplb follow  (or: nplb cancel)",
    ),
    ("block", break_block, "block file missing"),
    ("done", break_done, "row 1 is already done"),
    ("declared", break_declared, "declared nplb=on ar=off, the row needs nplb=off ar=off"),
    ("net_tools", break_net_tools, "the node state check failed"),
    ("validate", break_validate, "crab validate exited 1"),
    ("partition", break_partition, "the partition has 2 job(s): nodes are exclusive"),
]
FIXES = {
    "running": ["nplb follow", "nplb cancel"],
    "block": ["nplb campaign"],
    "done": ["nplb run 1 --force"],
    "declared": [
        "nplb net sheet nplb=off ar=off",
        "nplb net declare nplb=off ar=off",
        "nplb net set sr=off",
    ],
    "net_tools": ["no switch state declared: run nplb net declare", "nplb net show"],
    "validate": ["validate line 7", "validate line 3", "validate -p nplb_h100_gpu"],
    "partition": ["nplb queue", "nplb run 1 --force"],
}


@pytest.mark.parametrize("name,breaker,reason", GATES, ids=[g[0] for g in GATES])
def test_run_refuses_on_each_gate(world: World, name: str, breaker: object, reason: str) -> None:
    breaker(world)  # type: ignore[operator]
    before = world.runs_file.read_text(encoding="utf-8") if world.runs_file.exists() else None
    result = world.py("run", "1")
    assert result.returncode == 1, result.stdout + result.stderr
    first = result.stdout.splitlines()[0]
    assert first.startswith("VERDICT: REFUSED row 1: ") and reason in first
    for fix in FIXES[name]:
        assert fix in result.stdout
    assert world.submits() == []
    after = world.runs_file.read_text(encoding="utf-8") if world.runs_file.exists() else None
    assert after == before
    if name == "validate":
        assert "validate line 2" not in result.stdout  # only the last 5 lines
    assert max(len(line) for line in result.stdout.splitlines()) <= 100
    stamped(result.stderr)


def test_run_reports_every_failed_gate(world: World) -> None:
    break_declared(world)
    break_partition(world)
    break_validate(world)
    result = world.py("run", "1")
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert "declared nplb=on ar=off" in lines[0]  # the first failed gate is the verdict
    gates = [line for line in lines if re.match(r"gate \d/7 ", line)]
    assert [g.split(":")[0] for g in gates] == [
        "gate 4/7 switch state",
        "gate 6/7 crab validate",
        "gate 7/7 partition",
    ]


def test_run_refuses_a_wrong_nic_sr_and_a_missing_schedule(world: World) -> None:
    world.control("nic_sr", "1")
    result = world.py("run", "1")
    assert result.returncode == 1
    assert result.stdout.splitlines()[0].startswith("VERDICT: REFUSED row 1: the NICs have sr=on")
    assert "nplb net set sr=off" in result.stdout
    assert world.submits() == []
    (world.root / "camp" / "schedule.tsv").unlink()
    gone = world.py("run", "1")
    assert gone.returncode == 1
    assert gone.stdout.splitlines()[0] == "VERDICT: REFUSED row 1: no schedule: run: nplb campaign"
    unknown = world.py("run", "9")
    assert "no schedule" in unknown.stdout  # still the same refusal
    world.put_schedule(SIX)
    nine = world.py("run", "9")
    assert nine.returncode == 1
    assert "row 9 is not in the schedule (1-6)" in nine.stdout.splitlines()[0]


def test_run_refuses_a_busy_partition_unless_forced(world: World) -> None:
    world.control("partition_jobs", "4801\n")
    refused = world.py("run", "1")
    assert refused.returncode == 1
    assert "the partition has 1 job(s): nodes are exclusive" in refused.stdout
    assert world.submits() == []
    queue = [c for c in world.calls("squeue_calls") if "-p h100_4nodes" in c]
    assert queue and "-t RUNNING,PENDING" in queue[0] and "-o %i" in queue[0]
    forced = world.py("run", "1", "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert forced.stdout.splitlines()[-1] == "job 4711"
    assert len(world.submits()) == 1


def test_run_goes_on_without_squeue(world: World) -> None:
    result = world.py("run", "1", SQUEUE=str(world.bin / "no-such-squeue"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("squeue" in line and "not found" in line for line in stamped(result.stderr))


def test_run_next_uses_the_cursor_and_never_reruns(world: World) -> None:
    world.put_runs(run_line(1, "OK", elapsed=300), run_line(2, "WARN", elapsed=300))
    result = world.py("run", "next")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "submitted: row 3 job 4711" in result.stdout
    assert world.runs()[-1].n == 3

    world.put_runs(*[run_line(n, "OK", elapsed=300) for n in range(1, 7)])
    world.control("net_fail", "must not be called")
    done = world.py("run", "next")
    assert done.returncode == 0
    assert done.stdout.splitlines()[0] == "VERDICT: DONE all 6 rows"
    assert len(world.submits()) == 1  # still only the first submit

    world.control("net_fail", "")
    (world.fake / "net_fail").unlink()
    refused = world.py("run", "2")
    assert refused.returncode == 1
    assert "row 2 is already done" in refused.stdout.splitlines()[0]
    forced = world.py("run", "2", "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    again = [r for r in world.runs() if r.n == 2]
    assert [r.status for r in again] == ["OK", "SUBMITTED"]


@pytest.mark.parametrize("mode", ["fail", "nojob"])
def test_failed_submit_logs_nothing(world: World, mode: str) -> None:
    world.control("run_mode", mode)
    result = world.py("run", "1")
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert lines[0].startswith("VERDICT: FAIL submit")
    if mode == "fail":
        assert "boom: no allocation" in result.stdout
        assert "line1" not in result.stdout  # only the last 5 lines
    assert not world.runs_file.exists()
    assert not list((world.chk / "data").rglob("nplb_state.json"))
    assert "job 4711" not in result.stdout


@pytest.mark.parametrize("job", ["-1", "-x", "abc", "12 3", "1.5", "4711;x", "1_", "+3"])
def test_submit_with_a_bad_job_id_logs_nothing(world: World, job: str) -> None:
    world.control("job_id", job)
    result = world.py("run", "1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("VERDICT: FAIL submit")
    assert not world.runs_file.exists()
    assert not list((world.chk / "data").rglob("nplb_state.json"))


@pytest.mark.parametrize("job", ["4711", "123_4", "123+0", "123_4+1"])
def test_array_and_het_job_ids_are_accepted(world: World, job: str) -> None:
    world.control("job_id", job)
    result = world.py("run", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == f"job {job}"
    assert [r.job for r in world.runs()] == [job]


def test_verb_recognises_a_het_job_id(world: World) -> None:
    world.control("job_id", "123+0")
    result = world.nplb("run", "1", "--no-follow")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == "job 123+0"
    assert not result.stdout.startswith("run-")  # not paged: the verb saw the job line


@pytest.mark.parametrize("job", ["-x", "-", "abc", "-1"])
def test_cancel_refuses_a_log_job_that_is_not_a_job_id(world: World, job: str) -> None:
    world.put_runs(run_line(1, "SUBMITTED", job=job))
    result = world.py("cancel", NPLB_YES="1")
    assert result.returncode == 1
    assert "FAIL cancel:" in result.stdout and "is not a job id" in result.stdout
    assert world.calls("scancel_calls") == []
    assert world.runs()[0].status == "SUBMITTED"


def test_relative_data_dir_is_made_absolute(world: World) -> None:
    for mode, job in (("relative", "4711"), ("absolute", "4712")):
        world.control("run_mode", mode)
        world.control("job_id", job)
        world.put_runs()
        assert world.py("run", "1").returncode == 0
        (run,) = world.runs()
        want = world.chk / "data" / "nplb_h100_gpu" / "t1_split-i_a2a_2026-11-10_12-00-00"
        assert run.data_dir == str(want)
        assert Path(run.data_dir).is_absolute()


def test_local_rehearsal_rows_skip_the_network_gates(world: World) -> None:
    world.put_schedule([("1", "-", "a2a", 5.0), ("1", "-", "agtr", 6.0)])
    (world.root / "work" / "net_state.json").unlink()  # nothing declared
    world.control("net_fail", "must not be called")
    result = world.py("run", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert world.calls("net_calls") == []
    (run,) = world.runs()
    assert run.state == "-"
    data_dir = Path(run.data_dir)
    assert (data_dir / "schedule_row.json").is_file()
    assert not (data_dir / "nplb_state.json").exists()


# --- cancel ------------------------------------------------------------------------------------------


def test_cancel_asks_and_cancels_only_our_job(world: World) -> None:
    world.put_runs(run_line(1, "OK", elapsed=300), run_line(2, "SUBMITTED", job="4711"))
    declined = world.py("cancel")  # stdin is not a terminal and NPLB_YES is unset
    assert declined.returncode == 1
    assert "declined: nothing changed" in declined.stdout
    assert world.calls("scancel_calls") == []
    assert [r.status for r in world.runs()] == ["OK", "SUBMITTED"]

    world.control("scancel_rc", "1")
    failed = world.py("cancel", NPLB_YES="1")
    assert failed.returncode == 1
    assert "Invalid job id 4711" in failed.stdout and "second error line" not in failed.stdout
    assert world.runs()[-1].status == "SUBMITTED"

    (world.fake / "scancel_rc").unlink()
    (world.fake / "scancel_calls").unlink()
    done = world.py("cancel", NPLB_YES="1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        "job 4711 (row 2, " + B0 + ", t1 split-i a2a) submitted 2026-11-10T11:00:00" in done.stdout
    )
    assert world.calls("scancel_calls") == ["4711"]
    last = world.runs()[-1]
    assert (last.status, last.note, last.job) == ("CANCELLED", "cancelled by owner", "4711")
    assert world.runs()[0].status == "OK"

    nothing = world.py("cancel", NPLB_YES="1")
    assert nothing.returncode == 1 and "nothing to cancel" in nothing.stdout
    assert world.calls("scancel_calls") == ["4711"]


def test_cancel_takes_the_newest_submitted_attempt(world: World) -> None:
    world.put_runs(run_line(1, "SUBMITTED", job="4001"), run_line(2, "SUBMITTED", job="4002"))
    assert world.py("cancel", NPLB_YES="1").returncode == 0
    assert world.calls("scancel_calls") == ["4002"]
    assert [r.status for r in world.runs()] == ["SUBMITTED", "CANCELLED"]


def test_run_cmd_never_signals_a_process() -> None:
    text = (NPLB / "run_cmd.py").read_text(encoding="utf-8")
    for forbidden in ("os.kill", "killpg", "signal.", "import signal", "pkill"):
        assert forbidden not in text, forbidden


# --- skip --------------------------------------------------------------------------------------------


def test_parse_and_compress_helpers() -> None:
    assert run_cmd.parse_numbers(["5", "7", "9-12"]) == [5, 7, 9, 10, 11, 12]
    assert run_cmd.parse_numbers(["3", "3", "2-3"]) == [2, 3]
    for bad in (["x"], ["5-3"], ["0"], ["1-"], ["-4"]):
        with pytest.raises(ValueError):
            run_cmd.parse_numbers(bad)
    assert run_cmd.compress([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8"
    assert run_cmd.compress([4]) == "4"
    assert run_cmd.compress([]) == ""


def test_skip_selectors_and_report(world: World) -> None:
    world.put_runs(
        run_line(1, "OK", elapsed=300),
        run_line(2, "SUBMITTED", job="4242"),
        run_line(3, "FAIL", elapsed=9),
    )
    result = world.py("skip", "1-3", "5", "--why", "switch refuses\tthis state")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped 2 rows: 3, 5" in result.stdout.splitlines()
    assert "already handled: 1 (done), 2 (running)" in result.stdout.splitlines()
    skipped = [r for r in world.runs() if r.status == "SKIPPED"]
    assert [(r.n, r.job, r.data_dir, r.note) for r in skipped] == [
        (3, "-", "-", "switch refuses this state"),
        (5, "-", "-", "switch refuses this state"),
    ]
    assert (skipped[0].state, skipped[0].block, skipped[0].victim) == (B0, "t1", "ardc")
    assert skipped[0].submitted == NOW

    again = world.py("skip", "3", "5")
    assert again.returncode == 0
    assert "skipped 0 rows" in again.stdout
    assert "already handled: 3 (skipped), 5 (skipped)" in again.stdout

    by_state = world.py("skip", "--state", B1)  # rows 4, 5, 6; 5 is skipped already
    assert "skipped 2 rows: 4, 6" in by_state.stdout
    assert "already handled: 5 (skipped)" in by_state.stdout
    assert [r.note for r in world.runs() if r.n == 4] == ["skipped by owner"]

    by_tier = world.py("skip", "--tier", "1", "--state", B1)  # the union; row 3 and 4-6 handled
    assert by_tier.returncode == 0
    assert "skipped 0 rows" in by_tier.stdout

    before = world.runs_file.read_text(encoding="utf-8")
    for args in (["99"], ["1", "7", "9"], ["--state", "nope"], ["--tier", "9"]):
        bad = world.py("skip", *args)
        assert bad.returncode == 2, args
        assert world.runs_file.read_text(encoding="utf-8") == before
    assert "7, 9" in world.py("skip", "1", "7", "9").stderr
    assert world.py("skip").returncode == 2  # no selector
    assert world.py("skip", "x").returncode == 2

    undone = world.py("skip", "--undo", "3-4", "1")
    assert undone.returncode == 0, undone.stdout + undone.stderr
    assert "restored 2 rows: 3-4" in undone.stdout
    assert "not skipped: 1" in undone.stdout
    schedule = run_log.read_schedule(world.root / "camp" / "schedule.tsv")
    runs = world.runs()
    assert run_log.row_status(3, runs) == "FAILED"  # its FAIL attempt is still there
    assert run_log.row_status(4, runs) == "NEW"
    assert run_log.row_status(5, runs) == "SKIPPED"
    assert [r.status for r in runs if r.n == 4] == ["CANCELLED"]
    assert len(schedule) == 6


def test_next_after_skip_moves_on_and_shows_skipped(world: World) -> None:
    schedule = run_log.read_schedule(world.root / "camp" / "schedule.tsv")

    def attempt(n: int, status: str, elapsed: int | None = None) -> object:
        return run_log.Run(
            n,
            B0,
            "t1",
            "split-i",
            "a2a",
            "-" if status == "SKIPPED" else f"90{n}",
            "-" if status == "SKIPPED" else f"/d/{n}",
            NOW,
            status,
            elapsed,
            "",
        )

    runs = [attempt(1, "OK", 300), attempt(2, "SKIPPED"), attempt(3, "SKIPPED")]
    nxt = run_log.next_row(schedule, runs)
    assert nxt is not None and nxt.n == 4
    assert run_log.row_status(2, runs) == "SKIPPED"
    assert run_log.row_status(2, [*runs, attempt(2, "FAIL", 5)]) == "SKIPPED"
    assert run_log.row_status(2, [*runs, attempt(2, "OK", 5)]) == "DONE"
    assert run_log.row_status(2, [attempt(2, "SKIPPED"), attempt(2, "CANCELLED")]) == "SKIPPED"
    assert run_log.row_status(2, [attempt(2, "CANCELLED")]) == "NEW"
    assert run_log.retry_rows(schedule, runs) == []
    first, second = run_log.progress(schedule, runs)
    assert (first.rows, first.done, first.failed, first.skipped) == (3, 1, 0, 2)
    assert second.skipped == 0
    estimate = run_log.estimate_remaining(schedule, runs)
    assert estimate.remaining_min == pytest.approx(5.3 + 6.0 + 3.5)  # rows 2 and 3 are not in it

    world.put_runs(*[run_line(1, "OK", elapsed=300)])
    plain = world.py("skip", "2-3")
    assert plain.returncode == 0
    shown = subprocess.run(
        [sys.executable, str(world.chk / "examples" / "nplb" / "run_log.py"), "next"],
        env=world.env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = shown.stdout.splitlines()
    assert lines[0].startswith("VERDICT: NEXT row 4/6: ")
    assert "tier 1  1/3 done  0 failed  2 skipped  0.1 h elapsed  0.0 h left" in lines
    assert "tier 2  0/3 done  0 failed  0 skipped  0.0 h elapsed  0.2 h left" in lines
    assert "total   1/6 done  0 failed  2 skipped  0.1 h elapsed  0.2 h left" in lines
    assert any(line.startswith("remaining  0.2 h ") for line in lines)

    # a SUBMITTED attempt still blocks next, whatever is skipped
    with world.runs_file.open("a", encoding="utf-8") as handle:
        handle.write(run_line(4, "SUBMITTED", job="4242") + "\n")
    blocked = subprocess.run(
        [sys.executable, str(world.chk / "examples" / "nplb" / "run_log.py"), "next"],
        env=world.env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert blocked.stdout.splitlines()[0].startswith("VERDICT: RUNNING row 4 (job 4242)")

    # everything handled: DONE says how many rows were skipped
    world.put_runs(*[run_line(n, "OK", elapsed=300) for n in range(1, 6)])
    assert world.py("skip", "6").returncode == 0
    finished = world.py("run", "next")
    assert finished.stdout.splitlines()[0] == "VERDICT: DONE all 5 rows, 1 skipped"


# --- the dispatcher ----------------------------------------------------------------------------------


def test_dispatcher_run_cancel_skip(world: World) -> None:
    quick = world.nplb("run", "1", "--no-follow")
    assert quick.returncode == 0, quick.stdout + quick.stderr
    assert quick.stdout.splitlines()[-1] == "job 4711"
    assert [r.status for r in world.runs()] == ["SUBMITTED"]

    refused = world.nplb("run", "2")
    assert refused.returncode == 1
    paged = refused.stdout.splitlines()
    assert paged[0].startswith("run-gate | page 1/")
    assert paged[1].startswith("VERDICT: REFUSED row 2: job 4711 of row 1 is still running")
    assert len(paged) <= 38 and max(len(line) for line in paged) <= 100

    cancelled = world.nplb("cancel", NPLB_YES="1")
    assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
    assert world.calls("scancel_calls") == ["4711"]

    world.control("job_id", "4712")
    followed = world.nplb("run", "1")
    assert followed.returncode == 0, followed.stdout + followed.stderr
    lines = followed.stdout.splitlines()
    assert re.match(r"run-1 \| page 1/\d+", lines[0])
    assert lines[1].startswith("VERDICT: OK row 1 job 4712 ")
    assert "#report" not in followed.stdout and "job 4712" not in lines[0]
    assert any("Ctrl-C only stops following" in line for line in followed.stderr.splitlines())
    assert [r.status for r in world.runs()] == ["CANCELLED", "OK"]

    world.control("job_id", "4713")
    cursor = world.nplb("run", "next", "--no-follow")  # row 1 is done: the cursor is at row 2
    assert cursor.returncode == 0, cursor.stdout + cursor.stderr
    assert "submitted: row 2 job 4713" in cursor.stdout

    skipped = world.nplb("skip", "5")
    assert skipped.returncode == 0 and "skipped 1 row: 5" in skipped.stdout
    assert world.runs()[-1].status == "SKIPPED"

    helped = world.nplb("help")
    help_lines = helped.stdout.splitlines()
    for verb in ("run", "cancel", "skip"):
        assert any(line.startswith(f"  {verb} ") for line in help_lines), verb
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100

    for args in (
        ["run"],
        ["run", "abc"],
        ["skip"],
        ["skip", "x"],
        ["cancel", "extra"],
        ["run", "1", "--bogus"],
    ):
        bad = world.nplb(*args)
        assert bad.returncode == 2, (args, bad.stdout, bad.stderr)


def test_python_310_safe(world: World) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    result = subprocess.run(
        [python310, str(world.chk / "examples" / "nplb" / "run_cmd.py"), "skip", "5"],
        env=world.env(),
        cwd=world.root / "elsewhere",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped 1 row: 5" in result.stdout
