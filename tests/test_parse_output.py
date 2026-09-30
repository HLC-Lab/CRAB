"""A wrapper's parsed data is checked against its declared metrics before it is recorded.
Before, `read_data()` output went into the containers unchecked (runner.py collect loop) and
`log_data` truncated or padded mismatches, so a broken parser produced plausible CSVs."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from crab.core.data.containers import DataContainer
from crab.core.data.parse import ParseError, collect_run, parse_output


class _Proc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class _App:
    def __init__(self, data=None, n_metrics: int = 2, raises: Exception | None = None, rc: int = 0):
        self.id_num = 0
        self.collect_flag = True
        self.metadata = [{"name": f"m{i}", "unit": "s", "conv": True} for i in range(n_metrics)]
        self.process = _Proc(rc)
        self._data, self._raises = data, raises

    def read_data(self):
        if self._raises:
            raise self._raises
        return self._data


def test_well_formed_data_is_returned() -> None:
    assert parse_output(_App([[1.0, 2.0], [3, np.float64(4.5)]])) == [[1.0, 2.0], [3, 4.5]]


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ([[1.0]], "expected 2 metric series"),
        ([[1.0], [2.0], [3.0]], "expected 2 metric series"),
        ("1.0 2.0", "expected 2 metric series"),
        ([[1.0, 2.0], [3.0]], "different sample counts"),
        ([[1.0], []], "'m1' has no samples"),
        ([[1.0], ["fast"]], "'m1'.*not a number"),
        ([[1.0], [True]], "'m1'.*not a number"),
        (None, "expected 2 metric series"),
    ],
)
def test_malformed_data_raises_parse_error(data, why: str) -> None:
    with pytest.raises(ParseError, match=why):
        parse_output(_App(data))


def test_a_raising_parser_becomes_a_parse_error() -> None:
    with pytest.raises(ParseError, match="IndexError"):
        parse_output(_App(raises=IndexError("list index out of range")))


def _containers(apps: list[_App]) -> list[DataContainer]:
    return [DataContainer(a.id_num, True, m["name"], m["unit"]) for a in apps for m in a.metadata]


def test_collect_run_records_good_apps_and_reports_a_bad_one() -> None:
    good, bad = _App([[1.0], [2.0]]), _App([[1.0]])
    good.id_num, bad.id_num = 0, 1
    containers = _containers([good, bad])
    log = MagicMock()

    assert collect_run([good, bad], containers, log) is False
    assert [c.data for c in containers] == [[1.0], [2.0], [], []]
    assert "app 1" in log.error.call_args[0][0]


def test_collect_run_is_true_when_every_parse_succeeds() -> None:
    app = _App([[1.0, 2.0], [3.0, 4.0]])
    containers = _containers([app])
    assert collect_run([app], containers, MagicMock()) is True
    assert [c.num_samples for c in containers] == [[2], [2]]
