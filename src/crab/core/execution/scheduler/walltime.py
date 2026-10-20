"""The walltime of a local job, read in the formats of Slurm's `sbatch --time`."""

from __future__ import annotations

import re

ACCEPTED_FORMATS = "M, M:S, H:M:S, D-H, D-H:M or D-H:M:S (digits only; M alone is minutes)"

_NUMBER = r"([0-9]+)"
# Each pattern lists the units of its fields, in order; a bare number is minutes.
_PATTERNS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(_NUMBER), ("m",)),
    (re.compile(f"{_NUMBER}:{_NUMBER}"), ("m", "s")),
    (re.compile(f"{_NUMBER}:{_NUMBER}:{_NUMBER}"), ("h", "m", "s")),
    (re.compile(f"{_NUMBER}-{_NUMBER}"), ("d", "h")),
    (re.compile(f"{_NUMBER}-{_NUMBER}:{_NUMBER}"), ("d", "h", "m")),
    (re.compile(f"{_NUMBER}-{_NUMBER}:{_NUMBER}:{_NUMBER}"), ("d", "h", "m", "s")),
]
_SECONDS_PER = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_walltime(text: object) -> int:
    """The walltime `text` in seconds.

    Args:
        text: a Slurm time string: `M`, `M:S`, `H:M:S`, `D-H`, `D-H:M` or `D-H:M:S`.

    Raises:
        ValueError: `text` is not a string in one of those formats, has minutes or seconds of 60
            or more after its first field, is zero, or is `UNLIMITED`/`INFINITE` (a local job
            needs a finite walltime).
    """
    if isinstance(text, str) and text.strip().upper() in ("UNLIMITED", "INFINITE"):
        raise ValueError(f"walltime {text!r}: a local job needs a finite walltime")
    problem = f"walltime {text!r} is not valid; use {ACCEPTED_FORMATS}"
    if not isinstance(text, str):
        raise ValueError(problem)
    for pattern, units in _PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        fields = [int(part) for part in match.groups()]
        # Minutes and seconds are below 60, except as the leading field of the string.
        for position, (unit, value) in enumerate(zip(units, fields, strict=True)):
            if unit in ("m", "s") and value >= 60 and position > 0:
                raise ValueError(problem)
        total = sum(value * _SECONDS_PER[unit] for unit, value in zip(units, fields, strict=True))
        if total <= 0:
            raise ValueError(f"walltime {text!r} is zero; it must be positive")
        return total
    raise ValueError(problem)
