"""Rendering of progress stages for the CLI: one text line, plain reminders, a rich TTY line."""

from __future__ import annotations

import argparse
import io

from crab.cli import progress_render
from crab.cli.progress import NullReporter, Stage, stage, use_reporter
from crab.cli.progress_render import PlainReporter, RichReporter, for_args, render_line


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


S = Stage("ssh-connect", "Connecting", "the login node to accept the connection", 1000.0, 30)


def test_render_line_shows_label_wait_elapsed_and_limit():
    assert render_line(S, 1012.4) == (
        "Connecting: waiting for the login node to accept the connection "
        "(12 s, gives up after 30 s)"
    )


def test_render_line_without_a_limit_and_never_negative():
    s = Stage("install", "Installing", "pip", 1000.0)
    assert render_line(s, 1003.9) == "Installing: waiting for pip (3 s)"
    assert render_line(s, 990.0) == "Installing: waiting for pip (0 s)"


def test_render_line_uses_no_long_dash():
    assert "—" not in render_line(S, 1500.0) and "–" not in render_line(S, 1500.0)


def test_plain_reporter_prints_at_start_then_at_10_30_and_every_30_seconds():
    out, clock = io.StringIO(), Clock()
    rep = PlainReporter(out, clock=clock, ticker=False)
    rep.begin(S)
    assert out.getvalue().count("\n") == 1
    for t, lines in [(1005, 1), (1010, 2), (1020, 2), (1030, 3), (1059, 3), (1060, 4), (1090, 5)]:
        clock.now = t
        rep.tick()
        assert out.getvalue().count("\n") == lines, t
    assert "(10 s," in out.getvalue().splitlines()[1]
    assert "(30 s," in out.getvalue().splitlines()[2]


def test_plain_reporter_closing_line_only_when_the_stage_took_two_seconds_or_more():
    out, clock = io.StringIO(), Clock()
    rep = PlainReporter(out, clock=clock, ticker=False)
    rep.begin(S)
    clock.now = 1001.0
    rep.end("ssh-connect", True)
    assert out.getvalue().count("\n") == 1  # only the start line

    out2, clock2 = io.StringIO(), Clock()
    rep2 = PlainReporter(out2, clock=clock2, ticker=False)
    rep2.begin(S)
    clock2.now = 1002.0
    rep2.end("ssh-connect", True)
    assert out2.getvalue().splitlines()[-1] == "Connecting: done in 2 s"

    out3, clock3 = io.StringIO(), Clock()
    rep3 = PlainReporter(out3, clock=clock3, ticker=False)
    rep3.begin(S)
    clock3.now = 1031.0
    rep3.end("ssh-connect", False)
    assert out3.getvalue().splitlines()[-1] == "Connecting: failed after 31 s"


def test_plain_reporter_tracks_stages_independently():
    out, clock = io.StringIO(), Clock()
    rep = PlainReporter(out, clock=clock, ticker=False)
    outer = Stage("install", "Installing", "pip", 1000.0)
    rep.begin(outer)
    rep.begin(Stage("verify", "Verifying", "crab info", 1000.0))
    clock.now = 1010
    rep.tick()
    text = out.getvalue()
    assert "Installing: waiting for pip (10 s)" in text
    assert "Verifying: waiting for crab info (10 s)" in text
    rep.end("verify", True)
    rep.end("install", True)
    rep.tick()  # nothing left, nothing printed
    assert out.getvalue().splitlines()[-1] == "Installing: done in 10 s"


def test_rich_reporter_smoke_on_a_non_terminal_stream():
    rep = RichReporter(stream=io.StringIO())
    with use_reporter(rep):
        with stage("install", "Installing", "pip", 600):
            pass
    rep.close()


def test_for_args_is_silent_under_json_even_on_a_terminal():
    rep = for_args(argparse.Namespace(json=True), stream=io.StringIO(), is_tty=True)
    assert isinstance(rep, NullReporter)


def test_for_args_picks_rich_on_a_tty_and_plain_otherwise():
    rep = for_args(argparse.Namespace(json=False), stream=io.StringIO(), is_tty=True)
    assert isinstance(rep, RichReporter)
    rep.close()
    rep = for_args(argparse.Namespace(), stream=io.StringIO(), is_tty=False)
    assert isinstance(rep, PlainReporter)
    rep.close()


def test_for_args_detects_the_tty_from_the_stream_by_default():
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    rep = for_args(argparse.Namespace(json=False), stream=Tty())
    assert isinstance(rep, RichReporter)
    rep.close()


def test_reporting_makes_the_reporter_current_and_restores_it(capsys):
    from crab.cli.progress import current_reporter

    with progress_render.reporting(argparse.Namespace(json=True)):
        with stage("install", "Installing", "pip"):
            pass
    assert isinstance(current_reporter(), NullReporter)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_the_rich_line_never_reroutes_stdout(tmp_path):
    """A `print()` inside a stage reaches stdout even when stderr is a terminal (a pty here)."""
    import os
    import pty
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "import argparse\n"
        "from crab.cli.progress import stage\n"
        "from crab.cli.progress_render import reporting\n"
        "with reporting(argparse.Namespace(json=False)):\n"
        "    with stage('x', 'Working', 'a thing'):\n"
        "        print('PAYLOAD-ON-STDOUT', flush=True)\n"
    )
    src = Path(__file__).resolve().parent.parent / "src"
    master, slave = pty.openpty()
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=slave,
            env={**os.environ, "PYTHONPATH": str(src)},
            timeout=60,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert done.returncode == 0
    assert done.stdout.decode() == "PAYLOAD-ON-STDOUT\n"


def test_reporting_restores_the_previous_reporter_when_the_body_raises():
    import pytest

    from crab.cli.progress import current_reporter

    with pytest.raises(RuntimeError):
        with progress_render.reporting(argparse.Namespace(json=False)):
            raise RuntimeError("boom")
    assert isinstance(current_reporter(), NullReporter)
