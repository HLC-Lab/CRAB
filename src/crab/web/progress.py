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
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from typing import Any

from crab.cli.progress import Reporter, Stage, use_reporter
from crab.web.trackers import ExpiringTracker

PENDING_TTL = 30 * 60.0
_SEP = "\x00"


class _ClusterReporter:
    def __init__(self, board: ProgressBoard, cluster: str) -> None:
        self._board, self._cluster = board, cluster

    def begin(self, stage: Stage) -> None:
        self._board._stages[f"{self._cluster}{_SEP}{stage.stage}"] = {
            "status": "pending",
            **asdict(stage),
        }

    def end(self, stage_id: str, ok: bool) -> None:
        self._board._stages.pop(f"{self._cluster}{_SEP}{stage_id}", None)


class ProgressBoard:
    """Active stages keyed by cluster name; `local` is its own cluster."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        ttl_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._stages = ExpiringTracker(clock=ttl_clock, pending_ttl=PENDING_TTL)

    def reporter(self, cluster: str) -> Reporter:
        return _ClusterReporter(self, cluster)

    def now(self) -> float:
        """Epoch seconds: the clock the stages' `started_at` values are read against."""
        return self._clock()

    def snapshot(self, cluster: str) -> list[Stage]:
        """The cluster's active stages, oldest first."""
        prefix = f"{cluster}{_SEP}"
        found = [
            Stage(
                v["stage"],
                v["label"],
                v["waiting_for"],
                v["started_at"],
                v["timeout_s"],
            )
            for key, v in self._stages.items()
            if key.startswith(prefix)
        ]
        return sorted(found, key=lambda s: s.started_at)


def watching(request: Any, cluster: str) -> AbstractContextManager[Reporter]:
    """Make the app's board the current reporter for `cluster` around a handler's slow steps."""
    return use_reporter(request.app.state.progress.reporter(cluster))
