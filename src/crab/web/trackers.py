"""In-memory status trackers for background work (async submit, results fetch).

A tracker maps an id to a status dict (``{"status": "pending" | "done" |
"error", ...}``). The poll routes drop an entry once they return a terminal
status, but a client that never polls again (tab closed, backend left
running) would leave its entry behind for the whole process. Entries
therefore expire: ``TERMINAL_TTL`` after reaching a terminal status,
``PENDING_TTL`` after creation otherwise. Expired entries are swept lazily on
every access, so no background task is needed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

TERMINAL_TTL = 3600.0
PENDING_TTL = 24 * 3600.0


class ExpiringTracker:
    """id -> status dict, with lazy time-based expiry."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        # id -> (created_at, finished_at or None, status dict)
        self._entries: dict[str, tuple[float, float | None, dict[str, Any]]] = {}

    def _sweep(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, (created, finished, _value) in self._entries.items()
            if (finished is not None and now - finished >= TERMINAL_TTL)
            or now - created >= PENDING_TTL
        ]
        for key in expired:
            del self._entries[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        self._sweep()
        now = self._clock()
        created = self._entries[key][0] if key in self._entries else now
        finished = None if value.get("status") == "pending" else now
        self._entries[key] = (created, finished, value)

    def __getitem__(self, key: str) -> dict[str, Any]:
        self._sweep()
        return self._entries[key][2]

    def __contains__(self, key: object) -> bool:
        self._sweep()
        return key in self._entries

    def __len__(self) -> int:
        self._sweep()
        return len(self._entries)

    def get(self, key: str) -> dict[str, Any] | None:
        self._sweep()
        entry = self._entries.get(key)
        return entry[2] if entry is not None else None

    def pop(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._sweep()
        entry = self._entries.pop(key, None)
        return entry[2] if entry is not None else default
