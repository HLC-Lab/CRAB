"""`nplb diag run` (examples/nplb/diag_run.py): why a run failed, one paged report from a run folder.

Run folders are built in `tmp_path`: the failed all-to-all run from the real October 2026 excerpts
(tests/fixtures/nplb), a clean run, and synthetic crashes whose log shapes are guesses (no real
cross-leaf crash log exists off the lab node). The script runs as a subprocess and through the
dispatcher in a fake checkout. Nothing starts a job or sends a signal; no network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NPLB = REPO / "examples" / "nplb"
FIX = REPO / "tests" / "fixtures" / "nplb"
DIAG_PY = NPLB / "diag_run.py"
A2A_RUN = "gpu_a2a_comm_same_leaf_2026-10-29_16-04-13-621184"
AG_RUN = "gpu_ag_comm_same_leaf_2026-10-29_16-35-53-182977"
CROSS_RUN = "gpu_a2a_comm_cross_leaf_2026-10-29_00-00-00-000000"
NOW = "2026-11-10T12:00:00"


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "PAGE_LINES", "PAGE_COLS", "DATA_ROOT"):
        env.pop(var, None)
    env["DATA_ROOT"] = str(tmp_path / "data")
    env["WORK"] = str(tmp_path / "work")
    env["CAMPAIGN_DIR"] = str(tmp_path / "camp")
    env["NPLB_REPORT_DIR"] = str(tmp_path / "reports")
    env["NPLB_NOW"] = NOW
    env.update(extra)
    return env


def run_diag(
    tmp_path: Path, *args: str, python: str = sys.executable, **env: str
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    return subprocess.run(
        [python, str(DIAG_PY), *args],
        env=env_for(tmp_path, **env),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=60,
    )


def report_of(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The report lines of a successful run: stdout without its `#report NAME` header."""
    lines = result.stdout.splitlines()
    assert lines[0].startswith("#report diag-run-"), result.stdout + result.stderr
    return lines[1:]


def head_text(lines: list[str]) -> str:
    """The verdict paragraph (it wraps): the lines up to the first blank or `cause:` line."""
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.startswith(("cause:", "next:")):
            break
        out.append(line.strip())
    return " ".join(out)


# --- run folders ----------------------------------------------------------------------------------------


def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    return root


def a2a_folder(tmp_path: Path) -> Path:
    """The real failed run: 4 experiments, 3 failed apps (ppn4_alone app 10, ppn8_alone apps 9 and 10),
    the real error log of the first and metadata.csv listing both experiments FAILED."""
    root = data_root(tmp_path)
    folder = root / A2A_RUN
    (folder / "ppn4_alone").mkdir(parents=True)
    shutil.copy(FIX / "a2a_run1_crab_lines.txt", folder / "slurm_output.log")
    shutil.copy(FIX / "a2a_run1_metadata.csv", root / "metadata.csv")
    shutil.copy(FIX / "a2a_run1_error_app_10.log", folder / "ppn4_alone" / "error_app_10.log")
    apps = {str(k): {"args": f"-msgsize {2**k} -iter 10", "collect": True} for k in range(11)}
    apps["10"]["args"] = "-msgsize 536870912 -iter 100"
    config = {"experiments": {"ppn4_alone": {"apps": apps}, "ppn8_alone": {"apps": apps}}}
    (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return folder


def ag_folder(tmp_path: Path) -> Path:
    root = data_root(tmp_path)
    folder = root / AG_RUN
    folder.mkdir(parents=True)
    shutil.copy(FIX / "ag_run1_crab_lines.txt", folder / "slurm_output.log")
    shutil.copy(FIX / "ag_run1_metadata.csv", root / "metadata.csv")
    return folder


def ucx_line(i: int) -> str:
    return (
        f"[17932{i:05d}.123456] [nodeA:{72100 + i}:0]  ucp_ep.c:1234  UCX  ERROR "
        "Transport retry count exceeded"
    )


def cross_leaf_log() -> list[str]:
    return [
        "srun: error: nodeA: task 3: Exited with exit code 1",
        *[ucx_line(i) for i in range(37)],
        "application called MPI_Abort(MPI_COMM_WORLD, 1) - process 3",
        "srun: error: nodeA: task 0: Exited with exit code 1",
        "srun: error: nodeB: task 8: Exited with exit code 1",
        "srun: Terminating StepId=4242.0",
        "slurmstepd: error: *** STEP 4242.0 ON nodeA CANCELLED ***",
    ]


def crash_folder(tmp_path: Path, name: str, error_log: str) -> Path:
    folder = data_root(tmp_path) / name
    folder.mkdir(parents=True)
    (folder / "slurm_error.log").write_text(error_log, encoding="utf-8")
    return folder


def engine_failed(exp: str, app: int, second: int) -> str:
    return f"[12:00:{second:02d}] ERROR CRAB │ {exp} │ Run 1 │ App {app}  FAILED  exit=17"


def many_failures_folder(tmp_path: Path) -> Path:
    """14 failed apps with long error lines and 30 distinct long UCX lines: a report longer than a page."""
    folder = data_root(tmp_path) / "gpu_big_failure_2026-10-30_00-00-00-000000"
    log = ["[12:00:00] INFO CRAB  Worker started"]
    for k, exp in enumerate(["e1", "e2"], 1):
        log.append(f"[12:00:01] INFO CRAB  Starting experiment [{k}/2]: {exp}")
        for app in range(7):
            log.append(engine_failed(exp, app, 2 + app))
            (folder / exp).mkdir(parents=True, exist_ok=True)
            long_line = f"UCX  ERROR {exp} app {app} " + "very long explanation of the problem " * 6
            (folder / exp / f"error_app_{app}.log").write_text(
                f"App {app} exit=17\n\n--- STDOUT ---\n{long_line}\n", encoding="utf-8"
            )
    (folder / "slurm_output.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    ucx = [f"UCX  ERROR distinct failure number {i} " + "and many words " * 8 for i in range(30)]
    (folder / "slurm_error.log").write_text("\n".join(ucx) + "\n", encoding="utf-8")
    return folder


# --- red-first cases ------------------------------------------------------------------------------------


def test_real_a2a_failure(tmp_path: Path) -> None:
    a2a_folder(tmp_path)
    result = run_diag(tmp_path, A2A_RUN)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = report_of(result)
    assert lines[0].startswith("VERDICT: FAIL ")
    verdict = head_text(lines)
    assert A2A_RUN in verdict
    assert "3 failed apps in 2 of 4 experiments" in verdict
    assert "memory registration failed" in verdict
    assert any(line.startswith("cause: memory registration failed (ibv_reg_mr") for line in lines)
    next_line = next(line for line in lines if line.startswith("next:"))
    assert "536870912" in next_line and "nplb node diag nodeA" in next_line
    # the table: experiment, app, exit code from the engine's FAILED line, first error line
    row = next(line for line in lines if re.match(r"\s+ppn4_alone\s+10\s+17\s+ib_md\.c:296", line))
    assert "UCX ERROR ibv_reg_mr(address=0x7f1640000000" in row
    assert any(re.match(r"\s+ppn8_alone\s+9\s+17\s+\(no error_app log\)", x) for x in lines)
    assert any(re.match(r"\s+ppn8_alone\s+10\s+17\s+\(no error_app log\)", x) for x in lines)
    # the real shape is known: no "guesses" note for the memory registration class
    assert not any("guesses" in line for line in lines)
    # the two ib_md lines of the fixture differ only by their address: one row, counted twice
    ib_md = [x for x in lines if re.match(r"\s+x\d+\s+ib_md\.c:296 UCX ERROR ibv_reg_mr", x)]
    assert len(ib_md) == 1 and re.match(r"\s+x2\s", ib_md[0])


def test_clean_run(tmp_path: Path) -> None:
    ag_folder(tmp_path)
    result = run_diag(tmp_path, AG_RUN)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = report_of(result)
    assert lines[0] == f"VERDICT: OK {AG_RUN}: no failed app"


def test_cross_leaf_crash_without_app_logs(tmp_path: Path) -> None:
    log = cross_leaf_log()
    crash_folder(tmp_path, CROSS_RUN, "\n".join(log) + "\n")
    result = run_diag(tmp_path, CROSS_RUN)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = report_of(result)
    assert lines[0].startswith("VERDICT: FAIL ")
    assert CROSS_RUN in head_text(lines)
    assert any(x.startswith("cause: network / UCX transport error") for x in lines)
    next_line = next(x for x in lines if x.startswith("next:"))
    assert "nplb diag counters" in next_line and "nplb node diag nodeA" in next_line
    # each distinct UCX line once, with its repeat count
    ucx = [x for x in lines if "Transport retry count exceeded" in x and re.match(r"\s+x\d+ ", x)]
    assert len(ucx) == 1 and re.match(r"\s+x37\s+", ucx[0]), ucx
    assert any(re.match(r"\s+x1\s+application called MPI_Abort", x) for x in lines)
    # the last 8 lines of slurm_error.log, whitespace collapsed
    start = next(i for i, x in enumerate(lines) if x.startswith("slurm_error.log, last 8 lines"))
    shown = [x.strip() for x in lines[start + 1 : start + 9]]
    assert shown == [" ".join(x.split()) for x in log[-8:]]
    # one line that says the log shapes are guesses
    assert len([x for x in lines if "guesses" in x]) == 1
    assert not any(x.startswith("failed apps") for x in lines)


def test_unclassified_crash_is_never_given_a_cause(tmp_path: Path) -> None:
    text = 'Traceback (most recent call last):\n  File "run.py", line 3, in <module>\nValueError: boom\n'
    folder = crash_folder(tmp_path, "gpu_odd_crash_2026-10-30_00-00-00-000000", text)
    result = run_diag(tmp_path, folder.name)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = report_of(result)
    assert lines[0].startswith("VERDICT: FAIL ")
    assert head_text(lines).endswith("unclassified")
    assert any(x.startswith("cause: unclassified") for x in lines)
    start = next(i for i, x in enumerate(lines) if x.startswith("next:"))
    paragraph = "".join(x.strip() for x in lines[start : lines.index("", start)])  # it wraps
    assert str(folder / "slurm_error.log") in paragraph
    assert not any("guesses" in x for x in lines)
    assert any("ValueError: boom" in x for x in lines)


def test_empty_folder_is_unknown(tmp_path: Path) -> None:
    folder = data_root(tmp_path) / "gpu_empty_2026-10-30_00-00-00-000000"
    folder.mkdir()
    result = run_diag(tmp_path, folder.name)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = report_of(result)
    assert lines[0].startswith("VERDICT: UNKNOWN ")
    verdict = head_text(lines)
    assert "slurm_output.log" in verdict and "slurm_error.log" in verdict


def test_target_resolution(tmp_path: Path) -> None:
    older = ag_folder(tmp_path)
    newest = a2a_folder(tmp_path)
    os.utime(older, (1_000_000_000, 1_000_000_000))
    os.utime(newest, (1_900_000_000, 1_900_000_000))
    by_name = run_diag(tmp_path, A2A_RUN)
    by_path = run_diag(tmp_path, str(newest))
    by_default = run_diag(tmp_path)
    assert by_name.returncode == by_path.returncode == by_default.returncode == 1
    assert by_name.stdout == by_path.stdout == by_default.stdout
    assert report_of(by_name)[0].startswith("VERDICT: FAIL ")
    assert by_name.stdout.splitlines()[0] == f"#report diag-run-{A2A_RUN}"
    other = run_diag(tmp_path, AG_RUN)
    assert other.returncode == 0 and AG_RUN in other.stdout

    missing = run_diag(tmp_path, "no_such_run_folder")
    assert missing.returncode == 2
    assert "no_such_run_folder" in missing.stderr
    assert "Traceback" not in missing.stderr
    assert missing.stdout == ""


def test_nothing_to_read_exits_2(tmp_path: Path) -> None:
    missing = run_diag(tmp_path)
    assert missing.returncode == 2
    assert "nothing to follow" in missing.stderr and "Traceback" not in missing.stderr


def test_ansi_codes_are_not_shown(tmp_path: Path) -> None:
    red = "\x1b[31m"
    text = f"{red}UCX  ERROR\x1b[0m Transport retry count exceeded\n\x1b[2msecond\x1b[0m line\n"
    folder = crash_folder(tmp_path, "gpu_colour_2026-10-30_00-00-00-000000", text)
    result = run_diag(tmp_path, folder.name)
    assert "\x1b" not in result.stdout
    assert "Transport retry count exceeded" in result.stdout
    assert any(x.strip() == "second line" for x in report_of(result))


def test_read_only(tmp_path: Path) -> None:
    folder = a2a_folder(tmp_path)

    def snapshot() -> dict[str, tuple[int, int]]:
        found = {}
        for path in [tmp_path / "data", *(tmp_path / "data").rglob("*")]:
            stat = path.stat()
            found[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return found

    before = snapshot()
    result = run_diag(tmp_path, str(folder))
    assert result.returncode == 1
    assert snapshot() == before
    assert not (tmp_path / "work").exists() and not (tmp_path / "reports").exists()


def test_python_310_safe(tmp_path: Path) -> None:
    python310 = shutil.which("python3.10") or str(Path.home() / ".local" / "bin" / "python3.10")
    if not Path(python310).exists():
        pytest.skip("no python3.10 interpreter on this machine")
    ag_folder(tmp_path)
    result = run_diag(tmp_path, AG_RUN, python=python310)
    assert result.returncode == 0, result.stdout + result.stderr


# --- the dispatcher -------------------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


def run_nplb(tmp_path: Path, chk: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=env_for(tmp_path),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def saved_reports(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "reports").glob("*.txt"))


def test_dispatcher_end_to_end(tmp_path: Path, checkout: Path) -> None:
    a2a_folder(tmp_path)
    ag_folder(tmp_path)
    failed = run_nplb(tmp_path, checkout, "diag", "run", A2A_RUN)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    lines = failed.stdout.splitlines()
    assert lines[0].startswith(f"diag-run-{A2A_RUN} | page 1/")
    assert lines[1].startswith("VERDICT: FAIL ")
    saved = saved_reports(tmp_path)
    assert [p.name.split("_", 1)[1] for p in saved] == [f"diag-run-{A2A_RUN}.txt"]
    assert saved[0].read_text(encoding="utf-8").startswith("VERDICT: FAIL ")

    clean = run_nplb(tmp_path, checkout, "diag", "run", AG_RUN)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert f"VERDICT: OK {AG_RUN}: no failed app" in clean.stdout
    assert len(saved_reports(tmp_path)) == 2

    missing = run_nplb(tmp_path, checkout, "diag", "run", "no_such_run_folder")
    assert missing.returncode == 2
    assert "no_such_run_folder" in missing.stderr and "Traceback" not in missing.stderr
    assert len(saved_reports(tmp_path)) == 2


@pytest.mark.parametrize("args", [[], ["bogus"], ["counters-not-yet"]])
def test_dispatcher_usage(tmp_path: Path, checkout: Path, args: list[str]) -> None:
    result = run_nplb(tmp_path, checkout, "diag", *args)
    assert result.returncode == 2
    assert "usage: nplb diag run [TARGET]" in result.stderr
    assert result.stdout == ""


def test_dispatcher_too_many_arguments(tmp_path: Path, checkout: Path) -> None:
    result = run_nplb(tmp_path, checkout, "diag", "run", "a", "b")
    assert result.returncode == 2
    assert "usage" in result.stderr and "Traceback" not in result.stderr


def test_help_lists_diag(tmp_path: Path, checkout: Path) -> None:
    helped = run_nplb(tmp_path, checkout, "help")
    help_lines = helped.stdout.splitlines()
    assert any(line.startswith("  diag run") for line in help_lines)
    assert 0 < len(help_lines) <= 38 and max(len(line) for line in help_lines) <= 100


def test_every_page_fits_the_screen(tmp_path: Path, checkout: Path) -> None:
    folder = many_failures_folder(tmp_path)
    first = run_nplb(tmp_path, checkout, "diag", "run", folder.name)
    assert first.returncode == 1, first.stdout + first.stderr
    header = first.stdout.splitlines()[0]
    total = int(re.search(r"page 1/(\d+)", header).group(1))  # type: ignore[union-attr]
    assert total >= 2
    pages = [first.stdout]
    for n in range(2, total + 1):
        shown = run_nplb(tmp_path, checkout, "page", str(n))
        assert shown.returncode == 0, shown.stdout + shown.stderr
        pages.append(shown.stdout)
    for text in pages:
        lines = text.splitlines()
        assert 0 < len(lines) <= 38 and max(len(line) for line in lines) <= 100
    # the table is capped at 10 rows and says how many more
    whole = saved_reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "... 4 more" in whole
    assert "failed apps (14):" in whole
    # no saved line is wider than the screen: the pager had nothing to cut
    assert max(len(line) for line in whole.splitlines()) <= 100
