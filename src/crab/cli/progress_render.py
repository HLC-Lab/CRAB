"""Show progress stages in the terminal: a live line on a TTY, plain lines otherwise.

`render_line` is the one text for a stage. `RichReporter` redraws it on one updating line on
stderr with a live elapsed counter. `PlainReporter` writes lines to a stream: at the start, at
10 s, at 30 s, then every 30 s, and a closing line when the stage took 2 s or more. Under
`--json` nothing is printed: stdout stays JSON (ADR-002), and a stderr line would also leak into
the error detail some callers read from the output tail. Reporters write to stderr, never stdout.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TextIO

from crab.cli.progress import NullReporter, Reporter, Stage, use_reporter

CLOSING_MIN_S = 2.0
_FIRST_REMINDERS = (10.0, 30.0)
_REMINDER_EVERY_S = 30.0


def _elapsed(stage: Stage, now: float) -> int:
    return int(max(0.0, now - stage.started_at))


def render_line(stage: Stage, now: float) -> str:
    """`Label: waiting for <thing> (12 s, gives up after 30 s)` at time `now` (epoch seconds)."""
    detail = f"{_elapsed(stage, now)} s"
    if stage.timeout_s is not None:
        detail += f", gives up after {stage.timeout_s:g} s"
    return f"{stage.label}: waiting for {stage.waiting_for} ({detail})"


def _closing_line(stage: Stage, took: float, ok: bool) -> str:
    verb = "done in" if ok else "failed after"
    return f"{stage.label}: {verb} {int(took)} s"


def _next_reminder(elapsed: float) -> float:
    for mark in _FIRST_REMINDERS:
        if elapsed < mark:
            return mark
    return (elapsed // _REMINDER_EVERY_S + 1) * _REMINDER_EVERY_S


class PlainReporter:
    """Plain lines on `stream`; `tick()` prints the reminders that are due.

    With `ticker=True` a daemon thread calls `tick()` twice a second while a stage is open;
    tests pass `ticker=False` and call `tick()` with a fake clock.
    """

    def __init__(
        self,
        stream: TextIO,
        clock: Callable[[], float] = time.time,
        ticker: bool = True,
    ) -> None:
        self._stream = stream
        self._clock = clock
        self._ticker = ticker
        self._lock = threading.RLock()
        self._active: dict[str, tuple[Stage, float]] = {}  # id -> (stage, next reminder at)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()

    def begin(self, stage: Stage) -> None:
        with self._lock:
            self._active[stage.stage] = (stage, _next_reminder(0.0))
            self._write(render_line(stage, self._clock()))
            if self._ticker and self._thread is None:
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def end(self, stage_id: str, ok: bool) -> None:
        with self._lock:
            entry = self._active.pop(stage_id, None)
            if entry is None:
                return
            took = self._clock() - entry[0].started_at
            if took >= CLOSING_MIN_S:
                self._write(_closing_line(entry[0], took, ok))

    def tick(self) -> None:
        with self._lock:
            now = self._clock()
            for stage_id, (stage, due) in list(self._active.items()):
                elapsed = now - stage.started_at
                if elapsed >= due:
                    self._write(render_line(stage, now))
                    self._active[stage_id] = (stage, _next_reminder(elapsed))

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self.tick()

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)


class RichReporter:
    """One updating line (`rich.live.Live`) with a live elapsed counter, on `stream`."""

    def __init__(self, stream: TextIO, clock: Callable[[], float] = time.time) -> None:
        from rich.console import Console

        self._clock = clock
        self._console = Console(file=stream, highlight=False)
        self._live: Any = None
        self._lock = threading.RLock()
        self._active: dict[str, Stage] = {}

    def _renderable(self) -> Any:
        from rich.text import Text

        with self._lock:
            if not self._active:
                return Text("")
            current = list(self._active.values())[-1]
        return Text(render_line(current, self._clock()))

    def begin(self, stage: Stage) -> None:
        from rich.live import Live

        with self._lock:
            self._active[stage.stage] = stage
            if self._live is None:
                self._live = Live(
                    get_renderable=self._renderable,
                    console=self._console,
                    refresh_per_second=4,
                    transient=True,
                )
                self._live.start()

    def end(self, stage_id: str, ok: bool) -> None:
        from rich.text import Text

        with self._lock:
            stage = self._active.pop(stage_id, None)
            if stage is None:
                return
            took = self._clock() - stage.started_at
            if took >= CLOSING_MIN_S and self._live is not None:
                self._live.console.print(Text(_closing_line(stage, took, ok)))
            if not self._active and self._live is not None:
                self._live.stop()
                self._live = None

    def close(self) -> None:
        with self._lock:
            self._active.clear()
            if self._live is not None:
                self._live.stop()
                self._live = None


def for_args(
    args: Any,
    *,
    stream: TextIO | None = None,
    is_tty: bool | None = None,
    clock: Callable[[], float] = time.time,
) -> Any:
    """The reporter for a parsed command line: silent under `--json`, rich on a TTY, else plain."""
    if getattr(args, "json", False):
        return NullReporter()
    out = stream if stream is not None else sys.stderr
    tty = is_tty if is_tty is not None else bool(getattr(out, "isatty", lambda: False)())
    if tty:
        return RichReporter(out, clock=clock)
    return PlainReporter(out, clock=clock)


@contextmanager
def reporting(args: Any) -> Any:
    """Make the reporter for `args` current around the body, and stop its display after."""
    reporter: Reporter = for_args(args)
    try:
        with use_reporter(reporter):
            yield reporter
    finally:
        close = getattr(reporter, "close", None)
        if close is not None:
            close()
