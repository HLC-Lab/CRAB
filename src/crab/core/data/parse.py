"""Turning a wrapper's `read_data()` output into recorded samples, with shape checks.

A parse that raises or does not match the wrapper's declared `metadata` is a ParseError: the
app contributes nothing to that run and the run counts as failed, the same as a non-zero exit.
Nothing is truncated, padded or filled with zeros.
"""

from __future__ import annotations

import numbers
from typing import Any

from crab.core.data.containers import DataContainer


class ParseError(Exception):
    """A wrapper's output could not be turned into samples for its declared metrics."""


def parse_output(app: Any) -> list[list[Any]]:
    """The app's parsed samples, one list per declared metric, in `metadata` order.

    Raises:
        ParseError: if `read_data()` raises, or its result is not one non-empty list of
            numbers per metric with the same sample count for every metric.
    """
    try:
        raw = app.read_data()
    except Exception as exc:  # any parser bug must fail the run, not the experiment
        raise ParseError(f"read_data raised {type(exc).__name__}: {exc}") from exc

    metadata = app.metadata
    if not isinstance(raw, list | tuple) or len(raw) != len(metadata):
        got = f"{len(raw)} series" if isinstance(raw, list | tuple) else type(raw).__name__
        raise ParseError(
            f"expected {len(metadata)} metric series (one per metadata entry), got {got}"
        )

    series_out: list[list[Any]] = []
    for meta, series in zip(metadata, raw, strict=True):
        name = meta["name"]
        if not isinstance(series, list | tuple) or len(series) == 0:
            raise ParseError(f"metric {name!r} has no samples")
        for value in series:
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                raise ParseError(f"metric {name!r}: {value!r} is not a number")
        series_out.append(list(series))

    counts = {len(s) for s in series_out}
    if len(counts) > 1:
        per_metric = ", ".join(
            f"{m['name']}={len(s)}" for m, s in zip(metadata, series_out, strict=True)
        )
        raise ParseError(f"metrics have different sample counts ({per_metric})")
    return series_out


def collect_run(apps: list[Any], containers: list[DataContainer], log: Any) -> bool:
    """Record one run's samples into `containers`; False if any collected app failed to parse.

    Containers are laid out app by app, one per metric, for apps with `collect_flag` set.
    Apps that exited non-zero are skipped here (the caller already failed the run).
    """
    all_parsed = True
    c_idx = 0
    for app in apps:
        if not app.collect_flag:
            continue
        num_meta = len(app.metadata)
        if hasattr(app, "process") and app.process.returncode == 0:
            try:
                parsed = parse_output(app)
            except ParseError as exc:
                log.error(f"PARSE FAILED  app {app.id_num}: {exc}")
                all_parsed = False
            else:
                for i, series in enumerate(parsed):
                    containers[c_idx + i].data.extend(series)
                    containers[c_idx + i].num_samples.append(len(series))
        c_idx += num_meta
    return all_parsed
