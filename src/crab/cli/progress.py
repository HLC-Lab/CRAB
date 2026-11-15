"""Progress contract shared by the CLI and the web backend: what a slow step is doing.

A `Stage` names one step that can take a while: a stable id, what is happening, the thing it
waits for in plain words, when it started (epoch seconds) and the limit after which it gives up.
The elapsed time is never stored: a renderer computes `now - started_at`, so one record serves a
terminal line and a polled API answer alike.

A `Reporter` receives `begin` and `end`. The current reporter lives in a `ContextVar`, so a
background task created with `asyncio.create_task` inherits the reporter of the code that
started it. The default reporter prints nothing. Stage texts hold host names, ports and command
names only, never a password or a URL credential.

Standard library only: the web backend imports this module without pulling in the CLI.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Stage:
    """One slow step: `stage` is a stable kebab id, `label` says what is happening."""

    stage: str
    label: str
    waiting_for: str
    started_at: float
    timeout_s: float | None = None


class Reporter(Protocol):
    def begin(self, stage: Stage) -> None: ...

    def end(self, stage_id: str, ok: bool) -> None: ...


class NullReporter:
    """Reports nothing: the default, so code that is not watched pays nothing."""

    def begin(self, stage: Stage) -> None:
        pass

    def end(self, stage_id: str, ok: bool) -> None:
        pass


_SILENT = NullReporter()
_current: ContextVar[Reporter | None] = ContextVar("crab_progress_reporter", default=None)


def current_reporter() -> Reporter:
    return _current.get() or _SILENT


@contextmanager
def use_reporter(reporter: Reporter) -> Iterator[Reporter]:
    """Make `reporter` the current one for the body, then restore the previous one."""
    token = _current.set(reporter)
    try:
        yield reporter
    finally:
        _current.reset(token)


@contextmanager
def stage(
    stage_id: str,
    label: str,
    waiting_for: str,
    timeout_s: float | None = None,
    *,
    clock: Callable[[], float] = time.time,
) -> Iterator[None]:
    """Report one stage around the body; it ends with `ok=False` when the body raises."""
    reporter = current_reporter()
    reporter.begin(Stage(stage_id, label, waiting_for, clock(), timeout_s))
    ok = False
    try:
        yield
        ok = True
    finally:
        reporter.end(stage_id, ok)
