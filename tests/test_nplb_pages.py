"""examples/nplb/pages.py (the report pager), lib/pages.sh (its bash wrapper) and the nplb verbs
`page` and `reports`.

The engine runs as a subprocess with NPLB_REPORT_DIR under tmp_path and a fixed clock (NPLB_NOW),
so nothing depends on the wall clock or on real local state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
ENGINE = NPLB / "pages.py"
NOW = "2026-11-09T14:07:00"


def base_env(tmp_path: Path, **extra: str) -> dict:
    env = dict(os.environ)
    for var in (
        "NPLB_REPORT_DIR",
        "NPLB_WORK",
        "NPLB_NOW",
        "PAGE_LINES",
        "PAGE_COLS",
        "PYTHONPATH",
    ):
        env.pop(var, None)
    env["NPLB_REPORT_DIR"] = str(tmp_path / "reports")
    env["NPLB_NOW"] = NOW
    env.update(extra)
    return env


def engine(
    tmp_path: Path, *args: str, stdin: str = "", now: str = NOW, **env: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENGINE), *args],
        input=stdin,
        env=base_env(tmp_path, NPLB_NOW=now, **env),
        capture_output=True,
        text=True,
        timeout=60,
    )


def numbered(count: int) -> str:
    return "".join(f"line{i:03d}\n" for i in range(1, count + 1))


def content(stdout: str) -> list[str]:
    """The lines between the header and the footer."""
    return stdout.splitlines()[1:-1]


def page_count(tmp_path: Path, name: str, **env: str) -> int:
    header = engine(tmp_path, "page", "1", name, **env).stdout.splitlines()[0]
    return int(header.split("|")[1].split("/")[1])


def test_short_report_is_one_page(tmp_path):
    result = engine(tmp_path, "emit", "demo", stdin="a\nb\nc\nd\ne\n")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines == [
        "demo | page 1/1 | 14:07",
        "a",
        "b",
        "c",
        "d",
        "e",
        "-- end of demo (1 page) --",
    ]
    assert len(lines) <= 38


def test_long_report_splits_into_pages(tmp_path):
    text = numbered(100)
    first = engine(tmp_path, "emit", "demo", stdin=text)
    assert first.returncode == 0, first.stderr
    lines = first.stdout.splitlines()
    assert lines[0] == "demo | page 1/3 | 14:07"
    assert lines[-1] == "-- next: nplb page 2 (of 3) --"
    assert len(content(first.stdout)) == 36

    second = engine(tmp_path, "page", "2")
    third = engine(tmp_path, "page", "3")
    assert second.stdout.splitlines()[-1] == "-- next: nplb page 3 (of 3) --"
    assert third.stdout.splitlines()[-1] == "-- end of demo (3 pages) --"
    joined = content(first.stdout) + content(second.stdout) + content(third.stdout)
    assert joined == text.splitlines()


@pytest.mark.parametrize("count", [0, 1, 35, 36, 37, 72, 73, 500])
@pytest.mark.parametrize("width", [5, 99, 100, 101, 400])
def test_every_page_fits_the_screen(tmp_path, count, width):
    text = "".join("x" * width + "\n" for _ in range(count))
    first = engine(tmp_path, "emit", "fit", stdin=text)
    assert first.returncode == 0, first.stderr
    total = page_count(tmp_path, "fit")
    for k in range(1, total + 1):
        page = engine(tmp_path, "page", str(k), "fit").stdout.splitlines()
        assert 1 <= len(page) <= 38
        assert max(len(ln) for ln in page) <= 100


def test_wide_line_is_cut_with_a_tilde_and_kept_whole_on_disk(tmp_path):
    wide = "w" * 250
    result = engine(tmp_path, "emit", "wide", stdin=wide + "\n")
    assert content(result.stdout) == ["w" * 99 + "~"]
    saved = tmp_path / "reports" / "20261109T140700_wide.txt"
    assert saved.read_text() == wide + "\n"


def test_form_feed_forces_a_page_break(tmp_path):
    first = engine(tmp_path, "emit", "ff", stdin="a\n\f\nb\n")
    assert first.stdout.splitlines()[0] == "ff | page 1/2 | 14:07"
    assert content(first.stdout) == ["a"]
    second = engine(tmp_path, "page", "2")
    assert content(second.stdout) == ["b"]
    assert "\f" not in first.stdout + second.stdout


def test_ansi_colors_are_stripped(tmp_path):
    result = engine(tmp_path, "emit", "color", stdin="\x1b[31mred\x1b[0m\n")
    assert content(result.stdout) == ["red"]
    # the width is counted after stripping: 100 visible characters fit without a cut
    painted = "\x1b[31m" + "r" * 100 + "\x1b[0m\n"
    result = engine(tmp_path, "emit", "color2", stdin=painted)
    assert content(result.stdout) == ["r" * 100]


def test_empty_input_is_one_page_saying_so(tmp_path):
    result = engine(tmp_path, "emit", "nothing", stdin="")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "nothing | page 1/1 | 14:07",
        "(empty report)",
        "-- end of nothing (1 page) --",
    ]


def test_report_is_saved_and_latest_points_to_it(tmp_path):
    reports = tmp_path / "reports"
    engine(tmp_path, "emit", "demo", stdin="one\ntwo\n")
    saved = reports / "20261109T140700_demo.txt"
    assert saved.read_text() == "one\ntwo\n"
    latest = reports / "latest"
    assert latest.is_symlink()
    assert not os.path.isabs(os.readlink(latest))
    assert latest.resolve() == saved.resolve()

    engine(tmp_path, "emit", "other", stdin="x\n", now="2026-11-09T14:08:00")
    assert latest.resolve() == (reports / "20261109T140800_other.txt").resolve()


def test_same_second_same_name_never_overwrites(tmp_path):
    engine(tmp_path, "emit", "demo", stdin="first\n")
    engine(tmp_path, "emit", "demo", stdin="second\n")
    reports = tmp_path / "reports"
    assert (reports / "20261109T140700_demo.txt").read_text() == "first\n"
    assert (reports / "20261109T140700_demo-2.txt").read_text() == "second\n"
    # the newer one wins the "newest" lookup
    assert content(engine(tmp_path, "page", "1", "demo").stdout) == ["second"]


def test_name_is_restricted_to_safe_characters(tmp_path):
    engine(tmp_path, "emit", "a b/c", stdin="x\n")
    assert (tmp_path / "reports" / "20261109T140700_a_b_c.txt").exists()


def test_page_replays_by_number_and_by_report_name(tmp_path):
    engine(tmp_path, "emit", "alpha", stdin=numbered(100))
    engine(tmp_path, "emit", "beta", stdin="b1\nb2\n", now="2026-11-09T14:08:00")

    replay = engine(tmp_path, "page", "2", "alpha")
    assert replay.returncode == 0, replay.stderr
    assert replay.stdout.splitlines()[0] == "alpha | page 2/3 | 14:07"
    assert content(replay.stdout)[0] == "line037"

    newest = engine(tmp_path, "page", "1")
    assert newest.stdout.splitlines()[0] == "beta | page 1/1 | 14:08"

    prefix = engine(tmp_path, "page", "1", "al")
    assert prefix.stdout.splitlines()[0] == "alpha | page 1/3 | 14:07"

    whole = engine(tmp_path, "page", "1", "20261109T140700_alpha.txt")
    assert whole.stdout.splitlines()[0] == "alpha | page 1/3 | 14:07"

    unknown = engine(tmp_path, "page", "1", "zzz")
    assert unknown.returncode == 2
    assert str(tmp_path / "reports") in unknown.stderr
    assert "nplb reports" in unknown.stderr


def test_page_without_any_report_exits_2(tmp_path):
    result = engine(tmp_path, "page", "1")
    assert result.returncode == 2
    assert "nplb reports" in result.stderr or "no reports" in result.stderr


def test_next_and_prev_walk_the_report(tmp_path):
    engine(tmp_path, "emit", "walk", stdin=numbered(100))

    def header(*args: str) -> tuple[str, str]:
        result = engine(tmp_path, "page", *args)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        return lines[0], lines[-1]

    assert header("next")[0] == "walk | page 2/3 | 14:07"
    assert header("next")[0] == "walk | page 3/3 | 14:07"
    head, foot = header("next")
    assert head == "walk | page 3/3 | 14:07"
    assert foot == "-- end of walk (3 pages) --"
    assert header("prev")[0] == "walk | page 2/3 | 14:07"
    assert header("prev")[0] == "walk | page 1/3 | 14:07"
    assert header("prev")[0] == "walk | page 1/3 | 14:07"
    assert header("prev")[0] == "walk | page 1/3 | 14:07"
    assert (tmp_path / "reports" / ".cursor").is_file()


def test_cursor_follows_a_numbered_replay(tmp_path):
    engine(tmp_path, "emit", "alpha", stdin=numbered(100))
    engine(tmp_path, "emit", "beta", stdin="b\n", now="2026-11-09T14:08:00")
    engine(tmp_path, "page", "2", "alpha")
    result = engine(tmp_path, "page", "next")
    assert result.stdout.splitlines()[0] == "alpha | page 3/3 | 14:07"


def test_page_out_of_range_exits_2(tmp_path):
    engine(tmp_path, "emit", "demo", stdin=numbered(100))
    result = engine(tmp_path, "page", "9")
    assert result.returncode == 2
    assert "page 9 does not exist, the report has 3 pages" in result.stderr
    assert result.stdout == ""
    zero = engine(tmp_path, "page", "0")
    assert zero.returncode == 2


def test_reports_lists_newest_first(tmp_path):
    engine(tmp_path, "emit", "first", stdin="a\n", now="2026-11-09T14:07:00")
    engine(tmp_path, "emit", "second", stdin=numbered(100), now="2026-11-09T14:08:00")
    engine(tmp_path, "emit", "third", stdin="c\n", now="2026-11-10T09:30:00")
    result = engine(tmp_path, "reports")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "1  2026-11-10 09:30  third  1 page",
        "2  2026-11-09 14:08  second  3 pages",
        "3  2026-11-09 14:07  first  1 page",
    ]


def test_reports_listing_fits_one_page(tmp_path):
    for i in range(60):
        engine(tmp_path, "emit", f"r{i}", stdin="x\n", now=f"2026-11-09T14:{i:02d}:00")
    result = engine(tmp_path, "reports")
    lines = result.stdout.splitlines()
    assert result.returncode == 0
    assert len(lines) <= 38
    assert lines[0].startswith("1  ") and lines[0].endswith("r59  1 page")
    assert "older" in lines[-1]


def test_reports_without_any_says_so(tmp_path):
    result = engine(tmp_path, "reports")
    assert result.returncode == 0
    assert result.stdout.splitlines() == [f"no reports yet in {tmp_path / 'reports'}"]


def test_page_size_override(tmp_path):
    text = "".join(f"{i:03d}" + "z" * 57 + "\n" for i in range(50))
    result = engine(tmp_path, "emit", "small", stdin=text, PAGE_LINES="10", PAGE_COLS="40")
    assert result.returncode == 0, result.stderr
    assert len(content(result.stdout)) == 8
    total = page_count(tmp_path, "small", PAGE_LINES="10", PAGE_COLS="40")
    assert total == 7
    for k in range(1, total + 1):
        page = engine(
            tmp_path, "page", str(k), "small", PAGE_LINES="10", PAGE_COLS="40"
        ).stdout.splitlines()
        assert len(page) <= 10
        assert max(len(ln) for ln in page) <= 40


@pytest.mark.parametrize("env", [{"PAGE_LINES": "4"}, {"PAGE_COLS": "29"}, {"PAGE_LINES": "x"}])
def test_page_size_below_the_minimum_exits_2(tmp_path, env):
    result = engine(tmp_path, "emit", "demo", stdin="a\n", **env)
    assert result.returncode == 2
    assert "PAGE_" in result.stderr


def test_invalid_utf8_never_raises(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ENGINE), "emit", "bin"],
        input=b"ok\n\xff\xfe broken\n",
        env=base_env(tmp_path),
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert b"ok" in result.stdout


def test_emit_reads_a_file(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("from file\n")
    result = engine(tmp_path, "emit", "viafile", "--file", str(src))
    assert content(result.stdout) == ["from file"]
    missing = engine(tmp_path, "emit", "viafile", "--file", str(tmp_path / "nope"))
    assert missing.returncode == 2


# ---- bash wrapper ----


def run_bash(tmp_path: Path, script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env(tmp_path, **env),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_bash_wrapper_pipes_stdin_through_the_engine(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lib = NPLB / "lib" / "pages.sh"
    script = f'. "{lib}"; printf "x\\ny\\n" | nplb_paged demo; echo "rc=$?"; pwd; nplb_page 1 demo'
    result = run_bash(tmp_path, script, elsewhere)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "demo | page 1/1 | 14:07"
    assert lines[1:3] == ["x", "y"]
    assert "rc=0" in lines
    assert str(elsewhere.resolve()) in lines  # the caller's cwd is untouched
    assert lines.count("demo | page 1/1 | 14:07") == 2  # nplb_page replayed it


def test_bash_wrapper_keeps_shell_options_and_defines_only_its_own(tmp_path):
    lib = NPLB / "lib" / "pages.sh"
    script = (
        "before=$(compgen -A function | sort); opts_before=$-; "
        f'. "{lib}"; '
        "after=$(compgen -A function | sort); "
        '[[ $opts_before == "$-" ]] && echo same-opts; '
        'comm -13 <(echo "$before") <(echo "$after")'
    )
    result = run_bash(tmp_path, script, tmp_path)
    lines = result.stdout.splitlines()
    assert "same-opts" in lines
    new = [ln for ln in lines if ln != "same-opts"]
    assert sorted(new) == ["_nplb_pages_run", "nplb_page", "nplb_paged"]


# ---- the nplb dispatcher ----


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    shutil.copy(NPLB / "nplb", nplb_dir / "nplb")
    shutil.copy(NPLB / "pages.py", nplb_dir / "pages.py")
    shutil.copy(NPLB / "lib" / "pages.sh", nplb_dir / "lib" / "pages.sh")
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


def run_nplb(tmp_path: Path, chk: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = base_env(tmp_path, **env)
    full.pop("NPLB_REPORT_DIR", None)
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=full,
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=60,
    )


def emit_in(tmp_path: Path, chk: Path, name: str, text: str, **env: str):
    full = base_env(tmp_path, **env)
    full.pop("NPLB_REPORT_DIR", None)
    return subprocess.run(
        [sys.executable, str(chk / "examples" / "nplb" / "pages.py"), "emit", name],
        input=text,
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_nplb_verbs_page_and_reports(tmp_path, checkout):
    emitted = emit_in(tmp_path, checkout, "demo", numbered(50))
    assert emitted.returncode == 0, emitted.stderr
    default_dir = checkout / "local" / "nplb-work" / "reports"
    assert (default_dir / "20261109T140700_demo.txt").is_file()

    listing = run_nplb(tmp_path, checkout, "reports")
    assert listing.returncode == 0, listing.stderr
    assert listing.stdout.splitlines() == ["1  2026-11-09 14:07  demo  2 pages"]

    page = run_nplb(tmp_path, checkout, "page", "2", "demo")
    assert page.returncode == 0, page.stderr
    assert page.stdout.splitlines()[0] == "demo | page 2/2 | 14:07"

    # NPLB_WORK moves the reports dir
    work = tmp_path / "work"
    emit_in(tmp_path, checkout, "moved", "m\n", NPLB_WORK=str(work))
    assert (work / "reports" / "20261109T140700_moved.txt").is_file()
    listing = run_nplb(tmp_path, checkout, "reports", NPLB_WORK=str(work))
    assert listing.stdout.splitlines() == ["1  2026-11-09 14:07  moved  1 page"]


def test_nplb_help_lists_the_report_verbs(tmp_path, checkout):
    result = run_nplb(tmp_path, checkout, "help")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert any(ln.startswith("  page ") for ln in lines)
    assert any(ln.startswith("  reports ") for ln in lines)
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
