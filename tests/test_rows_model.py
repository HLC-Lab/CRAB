"""Wrapper contract v1 data model: `read_data()` returns rows (one dict per sample) with the
declared `keys` and metrics. Today's list-of-lists shape is still accepted and converted, and a
keyless wrapper's CSV is byte-identical to what CRAB wrote before rows existed."""

from __future__ import annotations

import itertools
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.data import DataContainer, check_CI, log_data
from crab.core.data.parse import ParseError, collect_run, parse_output, setup_containers

_ids = itertools.count()

# Captured from log_data before the rows model existed (two runs, three samples each).
GOLDEN_APP_0 = """run_id,msg_size,0_Avg-Duration_s,0_Min-Duration_s
1,1024,1.5e-05,7.5e-06
1,1024,2.25e-05,1.125e-05
1,1024,1.0,0.5
2,1024,3.125,1.5625
2,1024,0.1,0.05
2,1024,7.0,3.5
"""


class _Proc:
    returncode = 0


class _App:
    def __init__(self, runs: list, metadata: list[dict], keys: list[str] | None = None):
        self.id_num = next(_ids)
        self.collect_flag = True
        self.metadata = metadata
        self.keys = keys or []
        self.msg_size = 0
        self.process = _Proc()
        self._runs = iter(runs)

    def read_data(self):
        return next(self._runs)


def _m(name: str, unit: str = "s", conv: bool = True, **extra) -> dict:
    return {"name": name, "unit": unit, "conv": conv, **extra}


def _setup_containers(app: _App, msg_size: int = 0) -> list[DataContainer]:
    """What the runner pre-creates at setup for a keyless app."""
    app.msg_size = msg_size
    return setup_containers(app)


# ---- parse_output --------------------------------------------------------------------------


def test_rows_are_returned_as_given() -> None:
    app = _App([[{"size": 8, "lat": 1.5}, {"size": 16, "lat": 2.0}]], [_m("lat", "us")], ["size"])
    parsed = parse_output(app)
    assert parsed.rows == [{"size": 8, "lat": 1.5}, {"size": 16, "lat": 2.0}]
    assert parsed.legacy is False


def test_list_of_lists_is_lifted_to_rows() -> None:
    app = _App([[[1.0, 2.0], [3.0, 4.0]]], [_m("a"), _m("b")])
    parsed = parse_output(app)
    assert parsed.rows == [{"a": 1.0, "b": 3.0}, {"a": 2.0, "b": 4.0}]
    assert parsed.legacy is True


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ([], "no rows"),
        ([{"size": 8}], "row 0 is missing 'lat'"),
        ([{"lat": 1.0}], "row 0 is missing 'size'"),
        ([{"size": 8, "lat": 1.0, "bw": 3.0}], "row 0 has undeclared field 'bw'"),
        ([{"size": 8, "lat": "fast"}], "row 0: 'lat'.*not a number"),
        ([{"size": None, "lat": 1.0}], "row 0: key 'size'"),
        ([{"size": 8, "lat": 1.0}, [2.0]], "row 1 is not a dict"),
    ],
)
def test_malformed_rows_raise(rows: list, why: str) -> None:
    app = _App([rows], [_m("lat", "us")], ["size"])
    with pytest.raises(ParseError, match=why):
        parse_output(app)


def test_text_and_boolean_fields_are_allowed_when_declared() -> None:
    meta = [_m("t"), _m("status", "", False, type="str"), _m("valid", "", False, type="bool")]
    app = _App([[{"t": 1.0, "status": "PASSED", "valid": True}]], meta)
    assert parse_output(app).rows[0]["status"] == "PASSED"


# ---- CSV -----------------------------------------------------------------------------------


def test_a_legacy_wrapper_writes_the_same_csv_as_before(tmp_path: Path) -> None:
    run1 = [1.5e-05, 2.25e-05, 1.0]
    run2 = [3.125, 0.1, 7.0]
    app = _App(
        [[run1, [x / 2 for x in run1]], [run2, [x / 2 for x in run2]]],
        [_m("Avg-Duration"), _m("Min-Duration", conv=False)],
    )
    app.id_num = 0
    containers = _setup_containers(app, msg_size=1024)
    for run_id in (1, 2):
        assert collect_run([app], containers, MagicMock(), run_id=run_id)
    log_data("csv", str(tmp_path / "data"), containers)
    assert (tmp_path / "data_app_0.csv").read_text() == GOLDEN_APP_0


def test_keyed_rows_write_key_columns_after_msg_size(tmp_path: Path) -> None:
    app = _App(
        [
            [{"size": 8, "lat": 1.5}, {"size": 16, "lat": 2.5}],
            [{"size": 8, "lat": 1.25}, {"size": 16, "lat": 2.75}],
        ],
        [_m("lat", "us")],
        ["size"],
    )
    app.id_num = 3
    containers: list[DataContainer] = []
    for run_id in (1, 2):
        collect_run([app], containers, MagicMock(), run_id=run_id)
    log_data("csv", str(tmp_path / "data"), containers)
    assert (tmp_path / "data_app_3.csv").read_text() == (
        "run_id,msg_size,size,3_lat_us\n1,0,8,1.5\n1,0,16,2.5\n2,0,8,1.25\n2,0,16,2.75\n"
    )


def test_run_id_is_the_real_run_number(tmp_path: Path) -> None:
    app = _App([[[1.0]], [[2.0]]], [_m("t")])
    app.id_num = 0
    containers = _setup_containers(app)
    collect_run([app], containers, MagicMock(), run_id=2)  # run 1 failed: nothing collected
    collect_run([app], containers, MagicMock(), run_id=3)
    log_data("csv", str(tmp_path / "data"), containers)
    assert (tmp_path / "data_app_0.csv").read_text() == "run_id,msg_size,0_t_s\n2,0,1.0\n3,0,2.0\n"


def test_the_legacy_shape_warns_once_per_app() -> None:
    app = _App([[[1.0]], [[2.0]]], [_m("t")])
    log = MagicMock()
    containers = _setup_containers(app)
    collect_run([app], containers, log, run_id=1)
    collect_run([app], containers, log, run_id=2)
    assert log.warning.call_count == 1
    assert "rows" in log.warning.call_args[0][0]


# ---- convergence per key ---------------------------------------------------------------------


def test_each_key_of_a_sweep_converges_on_its_own() -> None:
    stable = [{"size": 8, "lat": 10.0}]
    runs = [stable + [{"size": 16, "lat": v}] for v in (1.0, 50.0, 3.0, 90.0, 7.0)]
    app = _App(runs, [_m("lat", "us")], ["size"])
    containers: list[DataContainer] = []
    for run_id in range(1, 6):
        collect_run([app], containers, MagicMock(), run_id=run_id)
    assert {c.key for c in containers} == {(("size", 8),), (("size", 16),)}
    assert check_CI(containers, alpha=0.05, beta=0.05, converge_all=False, run=5) is False
    by_size = {dict(c.key)["size"]: c.converged for c in containers}
    assert by_size == {8: True, 16: False}


def test_text_fields_never_block_or_break_convergence() -> None:
    meta = [_m("t"), _m("status", "", True, type="str")]
    app = _App([[{"t": 5.0, "status": "ok"}]] * 3, meta)
    containers = _setup_containers(app)
    for run_id in (1, 2, 3):
        collect_run([app], containers, MagicMock(), run_id=run_id)
    assert check_CI(containers, alpha=0.05, beta=0.05, converge_all=True, run=3) is True
