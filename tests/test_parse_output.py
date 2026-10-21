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


def test_well_formed_rows_are_returned() -> None:
    rows = parse_output(_App([{"m0": 1.0, "m1": 3}, {"m0": 2.0, "m1": np.float64(4.5)}])).rows
    assert rows == [{"m0": 1.0, "m1": 3}, {"m0": 2.0, "m1": 4.5}]


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ([{"m0": 1.0}], "row 0 is missing 'm1'"),
        ([{"m0": 1.0, "m1": 2.0, "m2": 3.0}], "row 0 has undeclared field 'm2'"),
        ([{"m0": 1.0, "m1": "fast"}], "row 0: 'm1' is not a number"),
        ([{"m0": 1.0, "m1": True}], "row 0: 'm1' is not a number"),
        ([{"m0": 1.0, "m1": 2.0}, [3.0]], "row 1 is not a dict"),
        ([], "read_data returned no rows"),
        ("1.0 2.0", "must return a list of rows"),
        (None, "must return a list of rows"),
    ],
)
def test_malformed_data_raises_parse_error(data, why: str) -> None:
    with pytest.raises(ParseError, match=why):
        parse_output(_App(data))


# The list-per-metric shape was removed in CRAB 1.0 (ADR-031): it is an error that names the
# wrapper, the removed shape and the docs page, with a one-line example of the rows shape.
LEGACY_RETURNS = [
    [[1.0, 2.0], [3.0, 4.0]],  # what the old parser lifted
    [[1.0], [2.0], [3.0]],  # wrong series count: still named as the old shape, not a count error
    [(1.0, 2.0), (3.0, 4.0)],
    [[], []],
]


@pytest.mark.parametrize("data", LEGACY_RETURNS)
def test_the_list_per_metric_shape_is_an_error(data) -> None:
    with pytest.raises(ParseError) as caught:
        parse_output(_App(data))
    message = str(caught.value)
    assert repr(_App.__module__) in message  # names the wrapper
    assert "one list of samples per metric" in message
    assert "removed in CRAB 1.0" in message
    assert '[{"m0": 1.0, "m1": 2.0}]' in message  # a one-line rows example
    assert "docs/extending/wrappers.md" in message


def test_the_legacy_error_example_uses_keys_when_declared() -> None:
    app = _App([[1.0], [2.0]])
    app.keys = ["size"]
    with pytest.raises(ParseError, match=r'\[\{"size": 8, "m0": 1.0, "m1": 2.0\}\]'):
        parse_output(app)


def test_a_legacy_return_fails_the_run_and_records_nothing() -> None:
    app = _App([[1.0], [2.0]])
    containers = _containers([app])
    log = MagicMock()
    assert collect_run([app], containers, log, run_id=1) is False
    assert [c.data for c in containers] == [[], []]
    assert "removed in CRAB 1.0" in log.error.call_args[0][0]
    log.warning.assert_not_called()


def test_an_app_without_metrics_may_return_no_rows() -> None:
    parsed = parse_output(_App([], n_metrics=0))
    assert parsed.rows == []


def test_a_raising_parser_becomes_a_parse_error() -> None:
    with pytest.raises(ParseError, match="IndexError"):
        parse_output(_App(raises=IndexError("list index out of range")))


def _containers(apps: list[_App]) -> list[DataContainer]:
    return [DataContainer(a.id_num, True, m["name"], m["unit"]) for a in apps for m in a.metadata]


def test_collect_run_records_good_apps_and_reports_a_bad_one() -> None:
    good, bad = _App([{"m0": 1.0, "m1": 2.0}]), _App([{"m0": 1.0}])
    good.id_num, bad.id_num = 0, 1
    containers = _containers([good, bad])
    log = MagicMock()

    assert collect_run([good, bad], containers, log, run_id=1) is False
    assert [c.data for c in containers] == [[1.0], [2.0], [], []]
    assert "app 1" in log.error.call_args[0][0]


def test_collect_run_is_true_when_every_parse_succeeds() -> None:
    app = _App([{"m0": 1.0, "m1": 3.0}, {"m0": 2.0, "m1": 4.0}])
    containers = _containers([app])
    assert collect_run([app], containers, MagicMock(), run_id=1) is True
    assert [c.num_samples for c in containers] == [[2], [2]]
