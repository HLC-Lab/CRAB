"""The `resolved_for` header of `config.resolved.json`, and the check that refuses a replay elsewhere (ADR-043).

A run folder holds the agnostic `config.json` and, when an app used `bind`, a copy `config.resolved.json`
that says which cluster description it was resolved against. The placement is not written out as literal
keys: the resolver is deterministic and versioned, so the same preset topology and resolver give the same
placement, and any other is refused, naming the difference.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import crab
from crab.core.execution.bind import Bind, parse_bind
from crab.core.execution.bind_resolve import RESOLVER_VERSION
from crab.core.execution.settings import ExecutionSettings
from crab.core.execution.topology import topology_sha256

RESOLVED_KEY = "resolved_for"


def has_bind(config: Mapping[str, Any]) -> bool:
    """True when some app of some experiment has a `bind` that asks for something.

    A `bind` that parses to the defaults (`Bind()`) places nothing, as in `apply_binds`, so it does not
    count. A `bind` that does not parse counts: validation reports it elsewhere.
    """
    for experiment in (config.get("experiments") or {}).values():
        apps = experiment.get("apps") if isinstance(experiment, dict) else None
        for app in (apps or {}).values():
            if isinstance(app, dict) and app.get("bind") and _asks_for_something(app["bind"]):
                return True
    return False


def _asks_for_something(bind: object) -> bool:
    try:
        return parse_bind(bind, "bind") != Bind()
    except ValueError:
        return True


def resolved_header(
    environment: Mapping[str, Any], settings: ExecutionSettings
) -> dict[str, str] | None:
    """What a resolved config was resolved against, or None when the preset has no topology."""
    if settings.topology is None:
        return None
    return {
        "preset": str(environment.get("CRAB_SYSTEM", "")),
        "topology_sha256": topology_sha256(settings.topology),
        "resolver_version": RESOLVER_VERSION,
        "crab_version": crab.__version__,
    }


def check_resolved_for(
    config: Mapping[str, Any], environment: Mapping[str, Any], settings: ExecutionSettings
) -> None:
    """Refuse a resolved config run with another preset, topology or resolver than it was resolved for.

    A config without `resolved_for` always passes.

    Raises:
        ValueError: `resolved_for` is malformed, or names a different preset, topology or resolver.
    """
    marker = config.get(RESOLVED_KEY)
    if marker is None:
        return
    keys = ("preset", "topology_sha256", "resolver_version")
    if not isinstance(marker, dict) or any(not isinstance(marker.get(k), str) for k in keys):
        raise ValueError(
            f"{RESOLVED_KEY}: must be an object with {', '.join(keys)} (as in config.resolved.json)"
        )
    now = resolved_header(environment, settings) or {
        "preset": str(environment.get("CRAB_SYSTEM", "")),
        "topology_sha256": "none",
        "resolver_version": RESOLVER_VERSION,
    }
    labels = {"preset": "preset", "topology_sha256": "topology", "resolver_version": "resolver"}
    differences = []
    for key in keys:
        if marker[key] != now[key]:
            shown = (lambda v: v[:12]) if key == "topology_sha256" else (lambda v: v)
            differences.append(f"{labels[key]} {shown(marker[key])!r} became {shown(now[key])!r}")
    if differences:
        raise ValueError(
            f"this config was resolved for another cluster setup ({'; '.join(differences)}), so it cannot "
            "run here: a resolved config is tied to the preset, topology and resolver it was resolved "
            "against. Run the config.json of the same run folder instead; it is not tied to any cluster."
        )
