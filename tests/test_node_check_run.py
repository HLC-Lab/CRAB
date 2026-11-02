"""Running the node check on every node of an experiment through its launcher (ADR-043).

Before: nothing looked at the nodes before the first run. The launcher is replaced by a fake that returns
what the per-node program would print, so these tests need neither Slurm nor MPI.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution import node_check_run
from crab.core.execution.node_check_run import (
    AppDevices,
    parse_answers,
    run_node_check,
    short,
    used_by,
)
from crab.core.execution.settings import LauncherSpec
from crab.core.execution.topology import topology_sha256
from crab.core.provenance import record_placement
from settings_fixtures import slurm_settings
from topology_fixtures import SHAPES

SRUN = LauncherSpec("srun", ("srun",), "", (), ())
DIRECT = LauncherSpec("direct", (), "", (), ())
FACTS = {"nics": {"nic0:1": "0000:01:00.0"}, "gpus": ["0000:02:00.0"], "numa": {"0": "0-7"}}


def answer(
    host: str, problems: list[str] | None = None, facts: dict[str, Any] | None = None
) -> str:
    return json.dumps({"host": host, "problems": problems or [], "facts": facts or FACTS})


class FakeLauncher:
    """Stands in for the launcher process: records what it was asked to run, prints what it is given."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.argv: list[str] = []
        self.calls = 0
        self.pauses: list[float] = []
        self.results = [subprocess.CompletedProcess([], returncode, stdout, stderr)]

    def then(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> FakeLauncher:
        """Another outcome for the next attempt; the last one repeats."""
        self.results.append(subprocess.CompletedProcess([], returncode, stdout, stderr))
        return self

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.argv = argv
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


def apps(*nodes: str) -> list[AppDevices]:
    return [AppDevices(nodes, ("nic0:1", "nic1:1,nic2:1"), (0, 1), (0, 8))]


def check(fake: FakeLauncher, spec: LauncherSpec = SRUN, *nodes: str, tmp: Path) -> list[str]:
    topology = SHAPES["four-gpus-four-nics"]()
    return run_node_check(
        topology, apps(*(nodes or ("n1", "n2"))), spec, str(tmp), fake, sleep=fake.pauses.append
    )


def test_used_by_collects_ports_gpu_use_and_cores_across_apps() -> None:
    used = used_by(
        [
            AppDevices(("n1",), ("a:1", "b:1,c:1"), (0,), (0, 8)),
            AppDevices(("n2",), (), (), (1,)),
        ]
    )
    assert used.nics == {"a:1", "b:1", "c:1"} and used.gpus is True and used.cores == {0, 1, 8}
    assert used_by([AppDevices(("n1",), (), (), ())]).gpus is False


def test_short_drops_the_domain() -> None:
    assert short("n1.cluster.local") == "n1" and short("n1") == "n1"


def test_parse_answers_keeps_node_lines_and_ignores_noise() -> None:
    out = "srun: info\n" + answer("n1.dom") + "\nnot json {\n{}\n" + answer("n2") + "\n"
    assert sorted(parse_answers(out)) == ["n1", "n2"]
    assert parse_answers("") == {}


def test_all_nodes_ok_and_alike_gives_no_problems(tmp_path: Path) -> None:
    fake = FakeLauncher(answer("n1") + "\n" + answer("n2.dom") + "\n")
    assert check(fake, tmp=tmp_path) == []


def test_the_launch_line_runs_the_check_module_one_task_per_node_with_a_timeout(
    tmp_path: Path,
) -> None:
    fake = FakeLauncher(answer("n1") + "\n" + answer("n2") + "\n")
    check(fake, tmp=tmp_path)
    assert fake.argv[:2] == ["bash", "-c"]
    line = fake.argv[2]
    assert line.startswith("timeout 150 srun ") and "--nodelist n1,n2 -n 2 -N 2 " in line
    assert " -m crab.core.execution.node_check " in line
    assert str(tmp_path / "node_check_spec.json") in line


def test_the_spec_file_holds_the_topology_and_what_the_apps_use(tmp_path: Path) -> None:
    check(FakeLauncher(answer("n1") + "\n" + answer("n2") + "\n"), tmp=tmp_path)
    spec = json.loads((tmp_path / "node_check_spec.json").read_text())
    assert spec["nics"] == ["nic0:1", "nic1:1", "nic2:1"]
    assert spec["gpus"] is True and spec["cores"] == [0, 8]
    assert spec["topology"]["gpu_vendor"] == "nvidia" and len(spec["topology"]["nics"]) == 4


def test_a_problem_on_one_node_is_reported_with_its_name(tmp_path: Path) -> None:
    fake = FakeLauncher(
        answer("n1") + "\n" + answer("n2", ["nic1:1 is not active (1: DOWN)"]) + "\n"
    )
    assert check(fake, tmp=tmp_path) == ["n2: nic1:1 is not active (1: DOWN)"]


def test_a_node_that_does_not_answer_is_named(tmp_path: Path) -> None:
    fake = FakeLauncher(answer("n1") + "\n", "srun: error: step failed\n")
    assert check(fake, tmp=tmp_path) == [
        "n2: no answer from the node check after 2 attempt(s) "
        "(launcher error tail: srun: error: step failed; raw output in node_check.log)"
    ]
    assert fake.calls == 2


def test_nodes_that_differ_from_each_other_are_named(tmp_path: Path) -> None:
    other = {**FACTS, "gpus": []}
    fake = FakeLauncher(answer("n1") + "\n" + answer("n2", facts=other) + "\n")
    assert check(fake, tmp=tmp_path) == ["n2 has 0 GPUs, n1 has 1"]


def test_a_launcher_that_prints_nothing_is_reported_with_its_error_tail(tmp_path: Path) -> None:
    fake = FakeLauncher("", "srun: error: a\nsrun: error: b\nsrun: error: c\nsrun: error: d\n", 1)
    assert check(fake, tmp=tmp_path) == [
        "the node check did not run (exit 1): srun: error: b | srun: error: c | srun: error: d"
    ]
    assert check(FakeLauncher("", "", 2), tmp=tmp_path) == [
        "the node check did not run (exit 2): no output"
    ]


def test_under_the_direct_launcher_only_this_host_answers_and_that_is_enough(
    tmp_path: Path,
) -> None:
    fake = FakeLauncher(answer("n1") + "\n")
    assert check(fake, DIRECT, tmp=tmp_path) == []
    assert fake.argv[2].startswith("timeout 150 ") and "srun" not in fake.argv[2]


def test_the_default_runner_executes_a_command_and_captures_its_output() -> None:
    done = node_check_run._subprocess_runner(["bash", "-c", "echo out; echo err >&2"])
    assert (done.returncode, done.stdout, done.stderr) == (0, "out\n", "err\n")


# --- the runner stops before the first run -----------------------------------------------------

_WRAPPER = (
    "from crab.wrappers.base import base\n\n"
    "class app(base):\n"
    "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
    "    def run_app(self):\n"
    "        return 'echo 1'\n"
)


def make_runner(tmp_path: Path, bind: dict[str, Any] | None):
    from crab.core.experiment.runner import ExperimentRunner

    path = tmp_path / "w0.py"
    path.write_text(_WRAPPER)
    app: dict[str, Any] = {"path": str(path), "collect": False, "ppn": 2}
    if bind is not None:
        app["bind"] = bind
    settings = dataclasses.replace(slurm_settings(), topology=SHAPES["four-gpus-four-nics"]())
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "out" / "crab_run.json").write_text(json.dumps({"results_schema": 1}))
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": {"0": app}, "local_options": {}},
        global_options={"numnodes": "1", "sbatch_directives": ["--exclusive"]},
        node_list=["n0"],
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
        settings=settings,
    )


def test_setup_runs_the_node_check_for_a_bind_app_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    def fake(topology: Any, devices: Any, spec: Any, spec_dir: str) -> list[str]:
        seen.append((devices, spec.kind, spec_dir))
        return []

    monkeypatch.setattr("crab.core.experiment.runner.run_node_check", fake)
    runner = make_runner(tmp_path, {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"})
    runner.setup()
    ((devices, kind, spec_dir),) = seen
    assert devices == [AppDevices(("n0",), ("nic0:1", "nic1:1"), (0, 1), (0, 1))]
    assert kind == "srun" and spec_dir == runner.exp_dir
    runner.log.info.assert_any_call("Node check passed on 1 node(s)")


def test_setup_stops_with_the_per_node_report_when_a_node_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "crab.core.experiment.runner.run_node_check",
        lambda *args: ["n0: nic1:1 is not active (1: DOWN)", "n1: no answer from the node check"],
    )
    runner = make_runner(tmp_path, {"gpu": "per-rank", "nic": "nearest"})
    with pytest.raises(RuntimeError) as error:
        runner.setup()
    assert str(error.value) == (
        "node check failed before the first run: the preset's topology does not describe these nodes\n"
        "  - n0: nic1:1 is not active (1: DOWN)\n"
        "  - n1: no answer from the node check"
    )


def test_an_experiment_without_bind_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any) -> list[str]:
        raise AssertionError("the node check must not run")

    monkeypatch.setattr("crab.core.experiment.runner.run_node_check", boom)
    make_runner(tmp_path, None).setup()
    make_runner(tmp_path, {}).setup()


def placements(tmp_path: Path) -> dict[str, Any]:
    data = json.loads((tmp_path / "out" / "crab_run.json").read_text())
    return data.get("placements", {})


def test_setup_records_the_resolved_placement_and_the_node_check_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("crab.core.experiment.runner.run_node_check", lambda *args: [])
    bind = {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}
    make_runner(tmp_path, bind).setup()
    record = placements(tmp_path)["exp"]
    assert record["resolver_version"] == "1"
    assert record["topology_sha256"] == topology_sha256(SHAPES["four-gpus-four-nics"]())
    app = record["apps"]["0"]
    assert (
        app["bind"] == bind
        and app["ppn"] == 2
        and app["warnings"]
        == [
            "app 0: the topology has no physical core list, so every core counts as physical (SMT unknown)"
        ]
    )
    assert app["ranks"] == [
        {
            "local_rank": 0,
            "gpu": 0,
            "nics": ["nic0:1"],
            "cores": [0],
            "dist": "PIX",
            "nic_share": 1,
        },
        {
            "local_rank": 1,
            "gpu": 1,
            "nics": ["nic1:1"],
            "cores": [1],
            "dist": "PIX",
            "nic_share": 1,
        },
    ]
    assert record["node_check"] == {"nodes": ["n0"], "passed": True, "problems": []}


def test_a_failed_node_check_still_leaves_its_problems_in_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "crab.core.experiment.runner.run_node_check",
        lambda *args: ["n0: nic0:1 is not on this node"],
    )
    with pytest.raises(RuntimeError, match="node check failed before the first run"):
        make_runner(tmp_path, {"nic": "spread"}).setup()
    check = placements(tmp_path)["exp"]["node_check"]
    assert check == {
        "nodes": ["n0"],
        "passed": False,
        "problems": ["n0: nic0:1 is not on this node"],
    }


def test_an_experiment_without_bind_records_no_placement(tmp_path: Path) -> None:
    make_runner(tmp_path, None).setup()
    assert placements(tmp_path) == {}


def test_record_placement_replaces_the_experiments_record_and_skips_a_missing_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "crab_run.json").write_text(json.dumps({"results_schema": 1}))
    record_placement(str(tmp_path), "e1", {"a": 1})
    record_placement(str(tmp_path), "e1", {"a": 2})
    record_placement(str(tmp_path), "e2", {"b": 1})
    data = json.loads((tmp_path / "crab_run.json").read_text())
    assert data["placements"] == {"e1": {"a": 2}, "e2": {"b": 1}} and data["results_schema"] == 1
    record_placement(str(tmp_path / "nowhere"), "e1", {"a": 1})


def test_a_node_that_misses_the_first_attempt_is_asked_again_and_the_check_passes(
    tmp_path: Path,
) -> None:
    fake = FakeLauncher(answer("n1") + "\n").then(answer("n1") + "\n" + answer("n2") + "\n")
    assert check(fake, tmp=tmp_path) == []
    assert fake.calls == 2 and fake.pauses == [3.0]


def test_nothing_is_repeated_when_every_node_answered(tmp_path: Path) -> None:
    fake = FakeLauncher(answer("n1") + "\n" + answer("n2") + "\n")
    check(fake, tmp=tmp_path)
    assert fake.calls == 1 and fake.pauses == []


def test_a_node_whose_check_crashed_is_asked_again_and_a_lasting_crash_is_named(
    tmp_path: Path,
) -> None:
    crashed = json.dumps(
        {
            "host": "n2",
            "problems": ["the node check crashed: FileNotFoundError: spec"],
            "facts": None,
        }
    )
    fake = FakeLauncher(answer("n1") + "\n" + crashed + "\n")
    assert check(fake, tmp=tmp_path) == ["n2: the node check crashed: FileNotFoundError: spec"]
    assert fake.calls == 2


def test_the_raw_output_of_every_attempt_is_kept_in_a_log(tmp_path: Path) -> None:
    fake = FakeLauncher("", "first error\n", 1).then(
        answer("n1") + "\n" + answer("n2") + "\n", "second note\n"
    )
    assert check(fake, tmp=tmp_path) == []
    log = (tmp_path / "node_check.log").read_text()
    assert "=== attempt 1: exit 1" in log and "first error" in log
    assert "=== attempt 2: exit 0" in log and "second note" in log and '"host": "n2"' in log


def test_a_single_attempt_is_not_retried(tmp_path: Path) -> None:
    fake = FakeLauncher(answer("n1") + "\n")
    problems = run_node_check(
        SHAPES["four-gpus-four-nics"](), apps("n1", "n2"), SRUN, str(tmp_path), fake, attempts=1
    )
    assert fake.calls == 1 and "after 1 attempt(s)" in problems[0]
