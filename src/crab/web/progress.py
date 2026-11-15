"""What each cluster's slow operation is doing right now, for the progress route.

The board is a `Reporter` per cluster name (see `crab.cli.progress`): a handler wraps its slow
step in `stage(...)` inside `use_reporter(board.reporter(name))`, and `GET
/api/remotes/{name}/progress` returns `snapshot(name)`. Stages live in memory only, like the
submit and fetch trackers (ADR-019). A stage ends in a `finally`, so `PENDING_TTL` is only the
net for an operation that vanished without ending (it must outlast the longest timeout, the
600 s install).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from typing import Any

from crab.cli.progress import Reporter, Stage, use_reporter
from crab.web.trackers import ExpiringTracker

PENDING_TTL = 30 * 60.0


class _ClusterReporter:
    """The reporter of one operation: it ends only the stages it began itself.

    Two requests on one cluster each get their own reporter, so one finishing never removes
    the stage the other still waits on, even when both run the same stage id.
    """

    def __init__(self, board: ProgressBoard, cluster: str) -> None:
        self._board, self._cluster = board, cluster
        self._open: dict[str, list[str]] = {}  # stage id -> tokens, newest last

    def begin(self, stage: Stage) -> None:
        token = uuid.uuid4().hex
        self._open.setdefault(stage.stage, []).append(token)
        self._board._stages[token] = {
            "status": "pending",
            "cluster": self._cluster,
            **asdict(stage),
        }

    def end(self, stage_id: str, ok: bool) -> None:
        tokens = self._open.get(stage_id)
        if not tokens:
            return
        self._board._stages.pop(tokens.pop(), None)
        if not tokens:
            del self._open[stage_id]


class ProgressBoard:
    """Active stages keyed by cluster name; `local` is its own cluster."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        ttl_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        # token -> the stage and its cluster; the token is private to one begin.
        self._stages = ExpiringTracker(clock=ttl_clock, pending_ttl=PENDING_TTL)

    def reporter(self, cluster: str) -> Reporter:
        return _ClusterReporter(self, cluster)

    def now(self) -> float:
        """Epoch seconds: the clock the stages' `started_at` values are read against."""
        return self._clock()

    def snapshot(self, cluster: str) -> list[Stage]:
        """The cluster's active stages, oldest first.

        A stage id that several operations have open shows once, with the oldest start.
        """
        found: dict[str, Stage] = {}
        for _token, v in self._stages.items():
            if v["cluster"] != cluster:
                continue
            candidate = Stage(
                v["stage"], v["label"], v["waiting_for"], v["started_at"], v["timeout_s"]
            )
            kept = found.get(candidate.stage)
            if kept is None or candidate.started_at < kept.started_at:
                found[candidate.stage] = candidate
        return sorted(found.values(), key=lambda s: s.started_at)


def watching(request: Any, cluster: str) -> AbstractContextManager[Reporter]:
    """Make the app's board the current reporter for `cluster` around a handler's slow steps."""
    return use_reporter(request.app.state.progress.reporter(cluster))
