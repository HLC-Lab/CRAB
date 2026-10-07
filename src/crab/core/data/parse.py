"""Turning a wrapper's `read_data()` output into recorded samples, with shape checks.

Wrapper contract v1: `read_data()` returns rows, one dict per sample, holding every declared
key (`keys`, the sweep dimensions such as a message size) and every metric in `metadata`. The
older shape, one list of samples per metric, is still accepted and converted to rows.

A parse that raises or does not match the declarations is a ParseError: the app contributes
nothing to that run and the run counts as failed, the same as a non-zero exit. Nothing is
truncated, padded or filled with zeros.
"""

from __future__ import annotations

import numbers
from dataclasses import dataclass
from typing import Any

from crab.core.data.containers import DataContainer

_TYPE_CHECKS = {
    "float": lambda v: isinstance(v, numbers.Real) and not isinstance(v, bool),
    "int": lambda v: isinstance(v, numbers.Integral) and not isinstance(v, bool),
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
}
_TYPE_WORDS = {"float": "a number", "int": "an integer", "str": "text", "bool": "true or false"}
_NUMERIC = ("float", "int")


class ParseError(Exception):
    """A wrapper's output could not be turned into samples for its declared metrics."""


@dataclass
class Parsed:
    rows: list[dict[str, Any]]
    # True when the wrapper returned the pre-v1 list-of-lists shape.
    legacy: bool


def _metric_type(meta: dict[str, Any]) -> str:
    kind = meta.get("type", "float")
    if kind not in _TYPE_CHECKS:
        raise ParseError(f"metric {meta['name']!r} declares unknown type {kind!r}")
    role = meta.get("role")
    if role not in (None, "check"):
        raise ParseError(f"metric {meta['name']!r} declares unknown role {role!r}")
    if role == "check" and kind != "bool":
        raise ParseError(f"check {meta['name']!r} must declare type bool")
    return kind


def _check_names(metadata: list[dict[str, Any]]) -> list[str]:
    return [m["name"] for m in metadata if m.get("role") == "check"]


def _lift_legacy(raw: list[Any], metadata: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(raw) != len(metadata):
        raise ParseError(
            f"expected {len(metadata)} metric series (one per metadata entry), got {len(raw)} series"
        )
    for meta, series in zip(metadata, raw, strict=True):
        if not isinstance(series, list | tuple) or len(series) == 0:
            raise ParseError(f"metric {meta['name']!r} has no samples")
    counts = [len(s) for s in raw]
    if len(set(counts)) > 1:
        per_metric = ", ".join(f"{m['name']}={n}" for m, n in zip(metadata, counts, strict=True))
        raise ParseError(f"metrics have different sample counts ({per_metric})")
    names = [m["name"] for m in metadata]
    return [dict(zip(names, values, strict=True)) for values in zip(*raw, strict=True)]


def _check_rows(rows: list[Any], metadata: list[dict[str, Any]], keys: list[str]) -> None:
    if not rows:
        raise ParseError("read_data returned no rows")
    declared = set(keys) | {m["name"] for m in metadata}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ParseError(f"row {i} is not a dict")
        for field in row:
            if field not in declared:
                raise ParseError(f"row {i} has undeclared field {field!r}")
        for key in keys:
            if key not in row:
                raise ParseError(f"row {i} is missing {key!r}")
            if not isinstance(row[key], str | numbers.Real):
                raise ParseError(f"row {i}: key {key!r} must be text or a number, got {row[key]!r}")
        for meta in metadata:
            name = meta["name"]
            if name not in row:
                raise ParseError(f"row {i} is missing {name!r}")
            kind = _metric_type(meta)
            if not _TYPE_CHECKS[kind](row[name]):
                raise ParseError(f"row {i}: {name!r} is not {_TYPE_WORDS[kind]}: {row[name]!r}")


def parse_output(app: Any) -> Parsed:
    """The app's parsed samples as rows, checked against its `keys` and `metadata`.

    Raises:
        ParseError: if `read_data()` raises or its result does not match the declarations.
    """
    try:
        raw = app.read_data()
    except Exception as exc:  # any parser bug must fail the run, not the experiment
        raise ParseError(f"read_data raised {type(exc).__name__}: {exc}") from exc

    metadata = app.metadata
    keys = list(getattr(app, "keys", []) or [])
    if not isinstance(raw, list | tuple):
        raise ParseError(
            f"expected {len(metadata)} metric series (one per metadata entry), "
            f"got {type(raw).__name__}"
        )
    # An empty result is only read as the old shape when no keys are declared.
    legacy = isinstance(raw[0], list | tuple) if raw else not keys
    if legacy and len(raw) == 0 and not metadata:
        return Parsed(rows=[], legacy=True)
    rows = _lift_legacy(list(raw), metadata) if legacy else list(raw)
    if legacy and keys:
        raise ParseError("a wrapper that declares keys must return rows")
    _check_rows(rows, metadata, keys)
    return Parsed(rows=rows, legacy=legacy)


def setup_containers(app: Any) -> list[DataContainer]:
    """Containers created before any run for a keyless app, one per metric (keyed apps get
    theirs as key values appear). Uses `app.msg_size`, the size parsed from the app's args."""
    if getattr(app, "keys", None):
        return []
    return [_new_container(app, meta, ()) for meta in app.metadata]


def _new_container(app: Any, meta: dict[str, Any], key: tuple) -> DataContainer:
    return DataContainer(
        app.id_num,
        meta["conv"],
        meta["name"],
        meta["unit"],
        getattr(app, "msg_size", 0),
        key=key,
        numeric=meta.get("type", "float") in _NUMERIC,
    )


def collect_run(apps: list[Any], containers: list[DataContainer], log: Any, *, run_id: int) -> bool:
    """Record run `run_id`'s samples into `containers`; False if any app failed to parse or a
    declared check was false (those rows are still recorded, but not used for convergence).

    There is one container per (app, metric, key values); new key values get a new container,
    appended to `containers`. Apps that did not run or exited non-zero are skipped here (the
    caller already failed the run).
    """
    index = {(c.app_id, c.label, c.key): c for c in containers}
    all_parsed = True
    for app in apps:
        if not app.collect_flag:
            continue
        if not (getattr(app, "process", None) is not None and app.process.returncode == 0):
            continue
        try:
            parsed = parse_output(app)
        except ParseError as exc:
            log.error(f"PARSE FAILED  app {app.id_num}: {exc}")
            all_parsed = False
            continue

        if parsed.legacy and not getattr(app, "_warned_legacy_shape", False):
            app._warned_legacy_shape = True
            log.warning(
                f"app {app.id_num} returns one list per metric; wrappers should return rows "
                "(one dict per sample). The old shape stops being accepted at v1.0."
            )

        keys = list(getattr(app, "keys", []) or [])
        checks = _check_names(app.metadata)
        failed_checks: set[str] = set()
        counts: dict[tuple, int] = {}
        for row in parsed.rows:
            key = tuple((k, row[k]) for k in keys)
            row_failed = [name for name in checks if row[name] is not True]
            failed_checks.update(row_failed)
            for meta in app.metadata:
                slot = (app.id_num, meta["name"], key)
                container = index.get(slot)
                if container is None:
                    container = _new_container(app, meta, key)
                    index[slot] = container
                    containers.append(container)
                container.data.append(row[meta["name"]])
                container.run_ids.append(run_id)
                container.valid.append(not row_failed)
                counts[slot] = counts.get(slot, 0) + 1
        for slot, count in counts.items():
            index[slot].num_samples.append(count)
        if failed_checks:
            names = ", ".join(repr(n) for n in sorted(failed_checks))
            log.error(f"CHECK FAILED  app {app.id_num}: {names} false in run {run_id}")
            all_parsed = False
    return all_parsed
