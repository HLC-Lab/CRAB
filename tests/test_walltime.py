"""`parse_walltime`: Slurm's `--time` formats, in seconds, for the local scheduler.

Slurm reads a bare number as minutes (`man sbatch`, `--time`): "minutes, minutes:seconds,
hours:minutes:seconds, days-hours, days-hours:minutes, days-hours:minutes:seconds".
"""

from __future__ import annotations

import pytest

from crab.core.execution.scheduler.walltime import parse_walltime


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("10", 600),
        ("5", 300),
        ("1:30", 90),
        ("00:10:00", 600),
        ("2-0", 172800),
        ("1-02:03", 93780),
        ("0-00:00:02", 2),
        ("1-02:03:04", 93784),
        ("100:00:00", 360000),
    ],
)
def test_accepted_formats(text: str, seconds: int) -> None:
    assert parse_walltime(text) == seconds


@pytest.mark.parametrize(
    "value",
    ["", "abc", "1:60", "1:2:60", "1:60:00", "0", "00:00:00", "0-0", "-1", "1:-1", "1.5", "1::2",
     " 5", "1-2-3", "1:2:3:4", "UNLIMITED", "infinite", None, 5, 5.0, True],
)  # fmt: skip
def test_refused_values_name_the_value(value: object) -> None:
    with pytest.raises(ValueError) as info:
        parse_walltime(value)

    assert repr(value) in str(info.value)


def test_an_unlimited_walltime_is_refused_with_a_local_reason() -> None:
    with pytest.raises(ValueError, match="a local job needs a finite walltime"):
        parse_walltime("UNLIMITED")
    with pytest.raises(ValueError, match="a local job needs a finite walltime"):
        parse_walltime("INFINITE")


def test_a_refused_value_lists_the_accepted_formats() -> None:
    with pytest.raises(ValueError, match="D-H:M:S"):
        parse_walltime("abc")
