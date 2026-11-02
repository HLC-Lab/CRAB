"""`msg_size_of` reads an app's message size from its `-msgsize` argument. These cases pin the rules
the runner applied inline before the function existed (the CSV `msg_size` column holds the result)."""

from __future__ import annotations

import pytest

from crab.core.data.msg_size import msg_size_of


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("-msgsize 1048576 -iter 50", 1048576),
        ("-iter 50 -msgsize 8", 8),
        ("-iter 50", 0),
        ("", 0),
        ("-msgsize abc", 0),
        ("-msgsize 1G", 0),  # a Blink-style suffix is not parsed
        ("-msgsize 1024.0", 0),
        ("-msgsize", 0),  # the flag is the last token, no value
        ("  -msgsize   64   -iter 5 ", 64),
        ("-msgsizes 99", 0),  # the flag only inside another word
        ("x-msgsize 99", 0),
        ("-msgsize 16 -msgsize 32", 16),  # the first occurrence wins
        ("-msgsize -5", -5),  # int() accepts a negative value; pinned, not endorsed
    ],
)
def test_msg_size_of(args: str, expected: int) -> None:
    assert msg_size_of(args) == expected


@pytest.mark.parametrize(
    ("args", "default", "expected"),
    [
        ("-iter 50", 1024, 1024),  # flag absent: the default is used
        ("", 1024, 1024),
        ("-msgsize 8", 1024, 8),  # an explicit flag beats the default
        ("-msgsize abc", 1024, 0),  # unparsable stays 0, not the default
        ("-msgsize", 1024, 0),  # flag last, no value: 0, not the default
    ],
)
def test_msg_size_of_default(args: str, default: int, expected: int) -> None:
    assert msg_size_of(args, default=default) == expected


def test_msg_size_of_without_default_is_zero_when_absent() -> None:
    assert msg_size_of("-iter 50") == 0
