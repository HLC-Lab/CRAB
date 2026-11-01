"""Parsing an app's placement keys: `ppn`, `cpus`, `env` and `nics`, and reading `--exclusive` (ADR-033).

Pure. Every error is a `ValueError`; callers add where it happened. `placement` re-exports these names,
so existing imports keep working.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from crab.core.execution.settings import check_dropped_keys


def parse_ppn(value: object) -> int:
    """A ranks-per-node value: an integer or an integer string, at least 1.

    Raises:
        ValueError: anything else (null, a float, a bool, an unresolved `{var}` token).
    """
    text = str(value).strip()
    if not re.fullmatch(r"\+?[0-9]+", text) or int(text) < 1:
        raise ValueError(f"ppn must be a positive integer, got {value!r}")
    return int(text)


def app_ppn(app_cfg: Mapping[str, Any], exp_opts: Mapping[str, Any]) -> int:
    """An app's ranks per node: its own `ppn`, else the experiment's, else 1.

    `exp_opts` is the experiment's options, `global_options` overlaid by its `local_options`.

    Raises:
        ValueError: the value that applies is not a positive integer (see `parse_ppn`).
    """
    return parse_ppn(app_cfg.get("ppn", exp_opts.get("ppn", 1)))


def parse_cpus(text: object, where: str) -> tuple[int, ...]:
    """Parse "0-3,8" into the sorted cores (0, 1, 2, 3, 8).

    Raises:
        ValueError: `text` is not a string of comma-separated `N` or `A-B` (A <= B) items, or
            a core appears twice.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}: cpus must be a core list such as '0-3,8', got {text!r}")
    cores: list[int] = []
    for item in text.split(","):
        item = item.strip()
        single = re.fullmatch(r"[0-9]+", item)
        span = re.fullmatch(r"([0-9]+)-([0-9]+)", item)
        if single:
            cores.append(int(item))
        elif span and int(span[1]) <= int(span[2]):
            cores.extend(range(int(span[1]), int(span[2]) + 1))
        else:
            raise ValueError(
                f"{where}: cpus {text!r} has the invalid item {item!r}; "
                "use N or A-B (A <= B), comma-separated"
            )
    if len(set(cores)) != len(cores):
        raise ValueError(f"{where}: cpus {text!r} lists a core more than once")
    return tuple(sorted(cores))


def parse_app_env(value: object, where: str) -> dict[str, str]:
    """An app's `env`: an object of strings, without the dropped `CRAB_*` launch keys.

    Raises:
        ValueError: `value` is not an object of strings, holds a dropped key, or has a key that
            is not a shell variable name (the app script exports it).
    """
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(f"{where}: must be an object of strings, got {value!r}")
    check_dropped_keys(value, where)
    for key in value:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{where}: key {key!r} is not a valid shell variable name")
    return dict(value)


def parse_app_nics(value: object, where: str) -> tuple[str, ...]:
    """An app's `nics`: a non-empty list of NIC device names such as "mlx5_0:1", kept in order.

    Raises:
        ValueError: `value` is not a non-empty list, or an item is not a string of letters,
            digits and `_ . : -` (the launch script puts it in `UCX_NET_DEVICES` unquoted).
    """
    if not isinstance(value, list) or not value:
        raise ValueError(
            f'{where}: must be a non-empty list of NIC device names such as "mlx5_0:1", '
            f"got {value!r}"
        )
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", item):
            raise ValueError(
                f"{where}: {item!r} is not a NIC device name (letters, digits, _ . : - only)"
            )
    return tuple(value)


def has_exclusive(sbatch_directives: Sequence[Any]) -> bool:
    """True when `--exclusive` is among the directives (list or dict form of each source)."""
    for source in sbatch_directives:
        if isinstance(source, dict):
            if any(str(k).lstrip("-") == "exclusive" and v is not False for k, v in source.items()):
                return True
        elif isinstance(source, list):
            for raw in source:
                words = str(raw).strip().lstrip("-").split()
                if words and words[0].split("=")[0] == "exclusive":
                    return True
    return False
