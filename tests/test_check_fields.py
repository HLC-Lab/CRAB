"""A metric declared with `role: "check"` is a correctness verdict (e.g. HPL's PASSED, NCCL's
zero out-of-bounds values). A false check keeps the row in the CSV, fails the run, and keeps
that sample out of convergence, so wrong-but-plausible numbers never mix into good ones."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.data import check_CI, log_data
from crab.core.data.parse import ParseError, collect_run, parse_output, setup_containers

META = [
    {"name": "gflops", "unit": "GF", "conv": True},
    {"name": "passed", "unit": "", "conv": False, "type": "bool", "role": "check"},
]


class _Proc:
    returncode = 0


class _App:
    def __init__(self, runs: list, metadata: list[dict] = META):
        self.id_num = 0
        self.collect_flag = True
        self.metadata = metadata
        self.keys: list[str] = []
        self.msg_size = 0
        self.process = _Proc()
        self._runs = iter(runs)

    def read_data(self):
        return next(self._runs)


def test_a_failed_check_fails_the_run_but_keeps_the_row(tmp_path: Path) -> None:
    app = _App([[{"gflops": 100.0, "passed": True}], [{"gflops": 5.0, "passed": False}]])
    containers = setup_containers(app)
    log = MagicMock()

    assert collect_run([app], containers, log, run_id=1) is True
    assert collect_run([app], containers, log, run_id=2) is False
    assert "passed" in log.error.call_args[0][0]

    log_data("csv", str(tmp_path / "d"), containers)
    assert (tmp_path / "d_app_0.csv").read_text().splitlines()[1:] == [
        "1,0,100.0,True",
        "2,0,5.0,False",
    ]


def test_failed_check_samples_are_left_out_of_convergence() -> None:
    good = [{"gflops": 100.0, "passed": True}]
    bad = [{"gflops": 1.0, "passed": False}]
    app = _App([good, bad, good, good])
    containers = setup_containers(app)
    for run_id in range(1, 5):
        collect_run([app], containers, MagicMock(), run_id=run_id)
    # Only the three identical good samples count, so gflops converges (sem == 0).
    assert check_CI(containers, alpha=0.05, beta=0.05, converge_all=False, run=4) is True


def test_a_check_must_be_declared_boolean() -> None:
    meta = [{"name": "oob", "unit": "", "conv": False, "role": "check"}]
    with pytest.raises(ParseError, match="check 'oob' must declare type bool"):
        parse_output(_App([[{"oob": 0}]], meta))


def test_an_unknown_role_is_refused() -> None:
    meta = [{"name": "x", "unit": "", "conv": False, "role": "verdict"}]
    with pytest.raises(ParseError, match="unknown role 'verdict'"):
        parse_output(_App([[{"x": 1.0}]], meta))
