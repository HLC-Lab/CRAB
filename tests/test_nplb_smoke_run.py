"""`nplb smoke` (examples/nplb/smoke_run.py): five smoke blocks, one after the other, one verdict page.

The driver runs as a subprocess behind the real dispatcher in a fake checkout under `tmp_path`, with
fake `crab`, `squeue`, `scontrol` and `scancel` scripts that log their arguments and obey small control
files in `$FAKE_DIR`; `crab run` creates a data dir whose `slurm_output.log` decides how `nplb follow`
ends the job. Nothing needs a cluster or ssh, and `scancel` only logs the job id it was given.
"""

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
NOW = "2026-11-10T12:00:00"
FEATURES = ["plain", "bursty", "ardc", "split-l", "shared"]
RUN_NAME = "smoke_{}_2026-11-10_12-00-00"  # the data dir the fake crab makes for a feature
FATAL_LOG = "Worker started\nWorker fatal error\n"


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

FAKE_CRAB = r"""#!/usr/bin/env bash
d=$FAKE_DIR
case $1 in
validate) exit 0 ;;
run)
    block=${@: -1}
    feature=$(basename "$block" .json)
    feature=${feature#smoke_}
    running=$(grep -c $'\tSUBMITTED\t' "$WORK/campaign_runs.tsv" 2>/dev/null)
    echo "$(date +%s.%N) $feature ${running:-0}" >>"$d/crab_runs"
    {
        echo "WORK=$WORK"
        echo "CAMPAIGN_DIR=$CAMPAIGN_DIR"
        echo "NPLB_REPORT_DIR=$NPLB_REPORT_DIR"
    } >>"$d/crab_env"
    n=$(cat "$d/jobs" 2>/dev/null || echo 5000)
    n=$((n + 1))
    echo "$n" >"$d/jobs"
    rel=data/nplb_h100_gpu/smoke_${feature}_2026-11-10_12-00-00
    mkdir -p "$rel"
    if [[ -f $d/log_$feature ]]; then
        cp "$d/log_$feature" "$rel/slurm_output.log"
    else
        printf 'Worker started\nEngine run finished  elapsed=0:02:00\n' >"$rel/slurm_output.log"
    fi
    printf '{"job_id": "%s", "data_dir": "%s"}\n' "$n" "$rel"
    ;;
esac
"""

FAKE_SQUEUE = r"""#!/usr/bin/env bash
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
exit 0
"""

# Row 2 is submitted but its follow is lost (Ctrl-C): the attempt stays SUBMITTED, exit 130.
INTERRUPT_BIN = r"""#!/usr/bin/env bash
if [[ ${1:-} == run && ${2:-} == 2 ]]; then
    bash "$REAL_NPLB" run 2 --no-follow >&2
    exit 130
fi
exec bash "$REAL_NPLB" "$@"
"""


class Lab:
    """A fake checkout, fake lab commands and a WORK folder, all under one folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.chk = root.resolve() / "chk"
        nplb_dir = self.chk / "examples" / "nplb"
        (nplb_dir / "lib").mkdir(parents=True)
        for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
            shutil.copy(path, nplb_dir / path.relative_to(NPLB))
        (nplb_dir / "nplb").chmod(0o755)
        self.dispatcher = nplb_dir / "nplb"
        self.fake = root / "fake"
        self.fake.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        scripts = {
            "crab": FAKE_CRAB,
            "squeue": FAKE_SQUEUE,
            "scontrol": FAKE_SCONTROL,
            "scancel": FAKE_SCANCEL,
        }
        for name, body in scripts.items():
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        (root / "work").mkdir()
        (root / "elsewhere").mkdir()

    @property
    def smoke_work(self) -> Path:
        return self.root / "work" / "smoke"

    @property
    def runs_file(self) -> Path:
        return self.smoke_work / "campaign_runs.tsv"

    def control(self, name: str, text: str) -> None:
        (self.fake / name).write_text(text, encoding="utf-8")

    def calls(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def runs(self) -> list[run_log.Run]:
        return run_log.read_runs(self.runs_file)

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        for var in (
            "PYTHONPATH",
            "NPLB_WORK",
            "CAMPAIGN_DIR",
            "NPLB_BIN",
            "NPLB_YES",
            "PAGE_LINES",
            "PAGE_COLS",
            "PRESET",
            "PARTITION",
            "DATA_ROOT",
        ):
            env.pop(var, None)
        env.update(
            FAKE_DIR=str(self.fake),
            CRAB=str(self.bin / "crab"),
            SQUEUE=str(self.bin / "squeue"),
            SCONTROL=str(self.bin / "scontrol"),
            SCANCEL=str(self.bin / "scancel"),
            WORK=str(self.root / "work"),
            NPLB_REPORT_DIR=str(self.root / "reports"),
            NPLB_NOW=NOW,
            FOLLOW_POLL="0.05",
            FOLLOW_STALL="1",
        )
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env.update(extra)
        return env

    def nplb(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.dispatcher), *args],
            env=self.env(**env),
            cwd=self.root / "elsewhere",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
        )

    def driver(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        """smoke_run.py itself: stdout is the summary, `#report smoke` first."""
        return subprocess.run(
            [sys.executable, str(self.chk / "examples" / "nplb" / "smoke_run.py"), *args],
            env=self.env(**env),
            cwd=self.root / "elsewhere",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
        )

    def interrupt_bin(self) -> str:
        script = self.root / "interrupt_nplb.sh"
        script.write_text(INTERRUPT_BIN, encoding="utf-8")
        script.chmod(0o755)
        return str(script)


def results(page: str) -> dict[str, str]:
    """feature -> result column of the table of a page."""
    found = {}
    for line in page.splitlines():
        match = re.match(rf"^({'|'.join(FEATURES)})\s+(OK|WARN|FAIL|not run|running)(\s|$)", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


@pytest.fixture(scope="module")
def ok_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Lab, subprocess.CompletedProcess[str]]:
    lab = Lab(tmp_path_factory.mktemp("ok"))
    return lab, lab.nplb("smoke")


@pytest.fixture(scope="module")
def fail_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Lab, subprocess.CompletedProcess[str]]:
    lab = Lab(tmp_path_factory.mktemp("fail"))
    lab.control("log_bursty", FATAL_LOG)
    return lab, lab.nplb("smoke")


# --- 1 to 4: the verdicts -------------------------------------------------------------------------------


def test_all_five_ok(ok_run: tuple[Lab, subprocess.CompletedProcess[str]]) -> None:
    lab, result = ok_run
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("smoke | page 1/1")
    assert lines[1].startswith("VERDICT: OK smoke: 5 of 5 features passed (")
    assert [(r.n, r.status) for r in lab.runs()] == [(n, "OK") for n in range(1, 6)]
    assert not (lab.root / "work" / "campaign_runs.tsv").exists()
    assert not (lab.root / "work" / "campaign").exists()
    assert set(results(result.stdout)) == set(FEATURES)
    assert set(results(result.stdout).values()) == {"OK"}
    assert "next: nplb topo, then nplb campaign" in result.stdout
    assert "nplb report does not list them" in result.stdout
    assert str(lab.smoke_work / "reports") in result.stdout


def test_rows_run_strictly_one_after_the_other(
    ok_run: tuple[Lab, subprocess.CompletedProcess[str]],
) -> None:
    lab, _result = ok_run
    calls = [line.split() for line in lab.calls("crab_runs")]
    assert [c[1] for c in calls] == FEATURES
    assert [c[2] for c in calls] == ["0"] * 5  # no attempt was still SUBMITTED at any submit
    stamps = [float(c[0]) for c in calls]
    assert stamps == sorted(stamps)


def test_one_failed_feature_does_not_stop_the_others(
    fail_run: tuple[Lab, subprocess.CompletedProcess[str]],
) -> None:
    lab, result = fail_run
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[1] == "VERDICT: FAIL smoke: bursty failed (4 of 5 passed)"
    assert [(r.n, r.status) for r in lab.runs()] == [
        (1, "OK"),
        (2, "FAIL"),
        (3, "OK"),
        (4, "OK"),
        (5, "OK"),
    ]
    assert results(result.stdout) == {
        "plain": "OK",
        "bursty": "FAIL",
        "ardc": "OK",
        "split-l": "OK",
        "shared": "OK",
    }
    table_end = max(i for i, line in enumerate(lines) if line.split()[:1] == ["shared"])
    assert lines[table_end + 1] == f"next: nplb diag run {RUN_NAME.format('bursty')}"
    assert RUN_NAME.format("bursty") in "\n".join(lines[table_end + 2 :])


def test_a_warn_counts_as_passed_and_is_named(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    data_root = lab.chk / "data" / "nplb_h100_gpu"
    data_root.mkdir(parents=True)
    name = RUN_NAME.format("plain")
    (data_root / "metadata.csv").write_text(
        f"relative_path,experiment_name,status,failed_runs\n./{name}/exp,exp,COMPLETED,1\n",
        encoding="utf-8",
    )
    result = lab.nplb("smoke")
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = result.stdout.splitlines()[1]
    assert verdict.startswith("VERDICT: OK smoke: 5 of 5 features passed (")
    assert verdict.endswith(", WARN: plain")
    assert results(result.stdout)["plain"] == "WARN"
    assert lab.runs()[0].status == "WARN"


# --- 5 and 6: the driver stops ----------------------------------------------------------------------------


def test_a_refusal_stops_at_row_one(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.control("partition_jobs", "4801\n")
    result = lab.nplb("smoke")
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines()[1].startswith("VERDICT: INCOMPLETE smoke: stopped at row 1")
    assert "0 of 5 done" in result.stdout.splitlines()[1]
    assert lab.runs() == []
    assert set(results(result.stdout).values()) == {"not run"}
    assert "REFUSED row 1" in result.stderr and "the partition has 1 job(s)" in result.stderr
    assert lab.calls("crab_runs") == []


def test_an_interrupted_row_stops_the_driver_and_says_the_job_keeps_running(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    result = lab.nplb("smoke", NPLB_BIN=lab.interrupt_bin(), REAL_NPLB=str(lab.dispatcher))
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[1].startswith("VERDICT: INCOMPLETE smoke: stopped at row 2")
    assert "1 of 5 done" in lines[1]
    assert [(r.n, r.status) for r in lab.runs()] == [(1, "OK"), (2, "SUBMITTED")]
    table = results(result.stdout)
    assert table["plain"] == "OK" and table["ardc"] == "not run" and table["shared"] == "not run"
    assert "keeps running" in result.stdout
    assert "nplb smoke follow" in result.stdout and "nplb smoke cancel" in result.stdout
    assert len(lab.calls("crab_runs")) == 2


# --- 7: re-run ------------------------------------------------------------------------------------------


def test_a_rerun_archives_the_old_files_and_starts_again(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    assert lab.nplb("smoke", "plain").returncode == 0
    first_log = lab.runs_file.read_text(encoding="utf-8")
    first_schedule = (lab.smoke_work / "campaign" / "schedule.tsv").read_text(encoding="utf-8")
    again = lab.nplb("smoke", "plain", "shared")
    assert again.returncode == 0, again.stdout + again.stderr
    (stamp,) = (lab.smoke_work / "archive").iterdir()
    assert re.fullmatch(r"20261110-120000", stamp.name)
    assert (stamp / "campaign_runs.tsv").read_text(encoding="utf-8") == first_log
    assert (stamp / "campaign" / "schedule.tsv").read_text(encoding="utf-8") == first_schedule
    assert [(r.n, r.block, r.status) for r in lab.runs()] == [
        (1, "plain", "OK"),
        (2, "shared", "OK"),
    ]
    assert len(lab.calls("crab_runs")) == 3


def test_a_rerun_refuses_while_a_smoke_job_is_running_and_moves_nothing(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.nplb("smoke", NPLB_BIN=lab.interrupt_bin(), REAL_NPLB=str(lab.dispatcher))
    before = lab.runs_file.read_text(encoding="utf-8")
    job = lab.runs()[-1].job
    result = lab.nplb("smoke")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"a smoke job is still running (job {job}): nplb smoke follow, or nplb smoke cancel" in (
        result.stderr
    )
    assert lab.runs_file.read_text(encoding="utf-8") == before
    assert (lab.smoke_work / "campaign" / "schedule.tsv").is_file()
    assert not (lab.smoke_work / "archive").exists()
    assert len(lab.calls("crab_runs")) == 2


# --- 8: feature selection -------------------------------------------------------------------------------


def test_features_can_be_chosen(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    result = lab.nplb("smoke", "plain", "shared")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[1].startswith("VERDICT: OK smoke: 2 of 2 features passed")
    assert [(r.n, r.block) for r in lab.runs()] == [(1, "plain"), (2, "shared")]
    assert set(results(result.stdout)) == {"plain", "shared"}


def test_an_unknown_feature_exits_2_before_anything_happens(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.nplb("smoke", "plain")
    before = lab.runs_file.read_text(encoding="utf-8")
    submits = len(lab.calls("crab_runs"))
    result = lab.nplb("smoke", "nonsense")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unknown feature 'nonsense'" in result.stderr
    for name in FEATURES:
        assert name in result.stderr
    assert result.stdout == ""
    assert lab.runs_file.read_text(encoding="utf-8") == before
    assert not (lab.smoke_work / "archive").exists()
    assert len(lab.calls("crab_runs")) == submits


# --- 9: follow, cancel, plan ----------------------------------------------------------------------------


def test_smoke_cancel_and_follow_act_on_the_smoke_run_log(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.nplb("smoke", NPLB_BIN=lab.interrupt_bin(), REAL_NPLB=str(lab.dispatcher))
    job = lab.runs()[-1].job
    assert lab.runs()[-1].status == "SUBMITTED"
    declined = lab.nplb("smoke", "cancel")
    assert declined.returncode == 1 and "declined" in declined.stdout
    assert lab.calls("scancel_calls") == []
    cancelled = lab.nplb("smoke", "cancel", NPLB_YES="1")
    assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
    assert lab.calls("scancel_calls") == [job]
    assert [r.status for r in lab.runs()] == ["OK", "CANCELLED"]
    assert not (lab.root / "work" / "campaign_runs.tsv").exists()


def test_smoke_follow_finishes_the_smoke_attempt(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.nplb("smoke", NPLB_BIN=lab.interrupt_bin(), REAL_NPLB=str(lab.dispatcher))
    assert lab.runs()[-1].status == "SUBMITTED"
    followed = lab.nplb("smoke", "follow")
    assert followed.returncode == 0, followed.stdout + followed.stderr
    assert [r.status for r in lab.runs()] == ["OK", "OK"]
    assert not (lab.root / "work" / "campaign_runs.tsv").exists()


def test_smoke_plan_estimate_writes_nothing(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    result = lab.nplb("smoke", "plan", "--estimate")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("smoke-plan | page 1/")
    assert lines[1].startswith("VERDICT: OK about ")
    assert not lab.smoke_work.exists()
    planned = lab.nplb("smoke", "plan", "plain")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert (lab.smoke_work / "campaign" / "schedule.tsv").is_file()
    assert not (lab.smoke_work / "campaign_runs.tsv").exists()
    assert lab.calls("crab_runs") == []


# --- 10 and 11: environment and size -------------------------------------------------------------------


def test_the_child_runs_see_the_smoke_work_folder(
    ok_run: tuple[Lab, subprocess.CompletedProcess[str]],
) -> None:
    lab, _result = ok_run
    smoke = str(lab.root / "work" / "smoke")
    seen = set(lab.calls("crab_env"))
    assert seen == {
        f"WORK={smoke}",
        f"CAMPAIGN_DIR={smoke}/campaign",
        f"NPLB_REPORT_DIR={smoke}/reports",
    }
    pages = sorted(p.name for p in (lab.smoke_work / "reports").iterdir())
    for n in range(1, 6):
        assert any(name.endswith(f"_run-{n}.txt") for name in pages), (n, pages)
    summary = [p.name for p in (lab.root / "reports").iterdir() if p.name.endswith("_smoke.txt")]
    assert len(summary) == 1
    assert not [p for p in (lab.root / "reports").iterdir() if "run-" in p.name]


def test_the_summary_fits_one_screen_with_a_long_data_dir_name(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    lab.control("log_bursty", FATAL_LOG)
    lab.control("log_shared", FATAL_LOG)
    result = lab.nplb("smoke")
    assert result.returncode == 1
    paged = result.stdout.splitlines()
    assert paged[0].startswith("smoke | page 1/1")
    assert len(paged) <= 38 and max(len(line) for line in paged) <= 100
    assert paged[1] == "VERDICT: FAIL smoke: bursty, shared failed (3 of 5 passed)"
    assert "next: nplb diag run " + RUN_NAME.format("bursty") in result.stdout
    assert not any(line.endswith("~") for line in paged)


def test_a_long_data_dir_name_is_not_cut(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    long_name = "smoke_bursty_" + "x" * 57
    lab.control("log_bursty", FATAL_LOG)
    wrapper = lab.root / "crab_long"
    wrapper.write_text(
        '#!/usr/bin/env bash\n"$REAL_CRAB" "$@" | sed "s/smoke_bursty_2026-11-10_12-00-00/'
        + long_name
        + '/"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    # the data dir of the long name is made by hand: crab prints it, the log sits in the real folder
    long_dir = lab.chk / "data" / "nplb_h100_gpu" / long_name
    long_dir.mkdir(parents=True)
    (long_dir / "slurm_output.log").write_text(FATAL_LOG, encoding="utf-8")
    result = lab.nplb("smoke", CRAB=str(wrapper), REAL_CRAB=str(lab.bin / "crab"))
    paged = result.stdout.splitlines()
    assert len(paged) <= 38 and max(len(line) for line in paged) <= 100
    assert f"next: nplb diag run {long_name}" in result.stdout


# --- 12: help ---------------------------------------------------------------------------------------------


def test_help_lists_smoke_and_stays_small(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    helped = lab.nplb("help")
    lines = helped.stdout.splitlines()
    assert 0 < len(lines) <= 30 and max(len(line) for line in lines) <= 100
    assert any(line.startswith("  smoke ") for line in lines)
    assert "smoke" in " ".join(lab.dispatcher.read_text(encoding="utf-8").splitlines()[4:6])


# --- 13 and 14: signals, Python 3.10 ---------------------------------------------------------------------


def test_smoke_run_never_signals_a_process() -> None:
    text = (NPLB / "smoke_run.py").read_text(encoding="utf-8")
    for forbidden in ("os.kill", "killpg", "signal.", "import signal", "pkill", "--force"):
        assert forbidden not in text, forbidden


def test_python_310_safe(tmp_path: Path) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    lab = Lab(tmp_path)
    result = subprocess.run(
        [python310, "-I", str(lab.chk / "examples" / "nplb" / "smoke_run.py"), "run", "plain"],
        env=lab.env(),
        cwd=lab.root / "elsewhere",
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "#report smoke"
