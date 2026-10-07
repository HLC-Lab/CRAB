"""Which chained apps run on another app's nodes (ADR-032).

An app with start "sN" waits for app N (its position in the runner's app order). It runs on the
nodes of the app it waits for, transitively, unless it names a different partition: then it is a
chain head of its own and gets a share of that partition like any other app.
"""

from __future__ import annotations


def resolve_chains(starts: list[str], partitions: list[str | None]) -> dict[int, int]:
    """Map each app that reuses nodes to the chain head whose nodes it runs on.

    Args:
        starts: each app's `start` value, in runner order ("0", "5.0", "s2", ...).
        partitions: each app's partition name, or None/"" for none.

    Raises:
        ValueError: if "sN" names no other app, starts form a cycle, or two apps would reuse the
            same app's nodes (they would run at the same time on them).
    """
    # Per app: (head whose nodes it runs on, effective partition). A chained app without a
    # partition is in its predecessor's partition.
    resolved: dict[int, tuple[int, str | None]] = {}

    def predecessor(i: int) -> int | None:
        start = str(starts[i])
        if not start.startswith("s"):
            return None
        target = start[1:]
        if not target.isdigit() or int(target) >= len(starts):
            raise ValueError(
                f"app {i}: start {start!r} names no app (apps are 0..{len(starts) - 1})"
            )
        return int(target)

    def resolve(i: int, path: list[int]) -> tuple[int, str | None]:
        if i in resolved:
            return resolved[i]
        if i in path:
            loop = " -> ".join(f"app {a}" for a in [*path[path.index(i) :], i])
            raise ValueError(f"start values form a cycle: {loop}")
        own = partitions[i] or None
        before = predecessor(i)
        if before is None:
            resolved[i] = (i, own)
            return resolved[i]
        head, partition = resolve(before, [*path, i])
        resolved[i] = (head, partition) if own in (None, partition) else (i, own)
        return resolved[i]

    reuse: dict[int, int] = {}
    reused_by: dict[int, int] = {}
    for i in range(len(starts)):
        head, _ = resolve(i, [])
        if head == i:
            continue
        reuse[i] = head
        before = predecessor(i)
        if before in reused_by:
            raise ValueError(
                f"apps {reused_by[before]} and {i} both start after app {before} and would run at "
                "the same time on its nodes; give one of them a different partition"
            )
        reused_by[before] = i
    return reuse
