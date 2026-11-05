"""The SbatchMan example campaigns launch as they are: every embedded CRAB config passes the checks.

A job in `examples/sbatchman/*.yaml` writes its `config.json` from a heredoc in `preprocess`, with
SbatchMan variables such as `{nodes}` and `{msgsize}` left in the text. These tests substitute every
value of every declared variable and run the engine's pre-submit checks on the result, so a typo in
a campaign shows up here and not on a cluster queue.

The two noise campaigns run the baseline and the noise as experiments of ONE job, so both sides see
the same physical nodes. The engine runs a config's experiments in sorted key order, so the names
`r1_baseline`, `r1_noise`, `r2_baseline`, ... sort into the alternation the campaigns promise.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import requires_wrappers_checkout
from crab.cli.validate import check_config_problems
from crab.core.wrapper_paths import wrapper_path_problem

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "sbatchman"
FILES = sorted(p for p in EXAMPLES.glob("*.yaml") if not p.name.startswith("configs."))
EXPECTED_FILES = {
    "smoke.yaml",
    "blink-noise.yaml",
    "a2a-vs-incast.yaml",
    "a2a-vs-a2a.yaml",
    "qe-alone.yaml",
    "qe-noise.yaml",
}
SWEEP_FILES = ["a2a-vs-incast.yaml", "a2a-vs-a2a.yaml"]
SWEEPS = [EXAMPLES / name for name in SWEEP_FILES]
NOISE_FILES = ["blink-noise.yaml", *SWEEP_FILES]
NOISE = [EXAMPLES / name for name in NOISE_FILES]
AGGRESSOR_PATH = {
    "blink-noise.yaml": "blink/incast/incast_nb.py",
    "a2a-vs-incast.yaml": "blink/incast/incast_nb.py",
    "a2a-vs-a2a.yaml": "blink/alltoall/alltoall_nb.py",
}
EXPERIMENTS = [
    "r1_baseline",
    "r1_noise",
    "r2_baseline",
    "r2_noise",
    "r3_baseline",
    "r3_noise",
]
# A quoted heredoc (<<'JSON') keeps the text as it is; the QE files use an unquoted one (<<JSON) so
# the shell expands $CRAB_ROOT in the input paths.
HEREDOC = re.compile(r"<<'?JSON'?\n(.*?)\nJSON\n", re.DOTALL)
QE_FILES = ["qe-alone.yaml", "qe-noise.yaml"]
QE_DIR = "$CRAB_ROOT/examples/sbatchman/qe"
QE_WRAPPER = "quantum-espresso/pw/v7.py"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _jobs(path: Path) -> list[dict[str, Any]]:
    return _load(path)["jobs"]


def _combinations(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every assignment of the file's variables (their Cartesian product)."""
    variables = doc["variables"]
    names = list(variables)
    return [
        dict(zip(names, values, strict=True)) for values in itertools.product(*variables.values())
    ]


def _tag(job: dict[str, Any], values: dict[str, Any]) -> str:
    tag = job["tag"]
    for name, value in values.items():
        tag = tag.replace("{" + name + "}", str(value))
    return tag


def _config_text(job: dict[str, Any]) -> str:
    match = HEREDOC.search(job["preprocess"])
    assert match, f"job {job.get('tag')}: no config.json heredoc in preprocess"
    return match.group(1)


def _config(job: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    text = _config_text(job)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return json.loads(text)


def _cases() -> list[Any]:
    return [
        pytest.param(path, job, values, id=f"{path.name}-{job['tag']}-{values}")
        for path in FILES
        for job in _jobs(path)
        for values in _combinations(_load(path))
    ]


def test_the_six_campaigns_are_shipped() -> None:
    assert {p.name for p in FILES} == EXPECTED_FILES


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_file_has_the_shape_the_dashboard_writes(path: Path) -> None:
    doc = _load(path)

    assert doc["configs"] == "configs.leonardo.yaml"
    assert doc["variables"]["nodes"]
    assert len(doc["jobs"]) == 1
    for job in doc["jobs"]:
        assert job["config"] == "crab_run_{nodes}"
        assert "{nodes}" in job["tag"]
        assert HEREDOC.search(job["preprocess"])
        assert 'mkdir -p "$SBATCHMAN_JOB_DIR"' in job["preprocess"]
        assert 'cat > "$SBATCHMAN_JOB_DIR/config.json"' in job["preprocess"]
        assert job["command"] == 'crab worker --workdir "$SBATCHMAN_JOB_DIR"'


@pytest.mark.parametrize("path", SWEEPS, ids=lambda p: p.name)
def test_the_noise_files_sweep_nodes_and_message_size_one_job_each(path: Path) -> None:
    doc = _load(path)
    job = doc["jobs"][0]

    assert doc["variables"] == {
        "nodes": [4, 8, 16],
        "msgsize": [1024, 65536, 1048576, 4194304],
    }
    assert len(_combinations(doc)) == 12
    assert "{nodes}" in job["tag"]
    assert "{msgsize}" in job["tag"]
    # A tag is the job's name in SbatchMan: it differs for every (nodes, msgsize) pair.
    tags = {_tag(job, values) for values in _combinations(doc)}
    assert len(tags) == 12


@pytest.mark.parametrize(
    "path", [*sorted(EXAMPLES.glob("*.yaml")), EXAMPLES / "README.md"], ids=lambda p: p.name
)
def test_no_example_names_the_removed_preset_command(path: Path) -> None:
    assert "sbatchman-preset" not in path.read_text()


def test_the_readme_tells_the_partner_to_check_time_and_qos() -> None:
    lines = (EXAMPLES / "README.md").read_text().splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("2. In your SbatchMan project directory")
    )
    end = next(i for i, line in enumerate(lines) if line.startswith("3. Copy a jobs file"))
    step_two = "\n".join(lines[start:end])

    assert "`time`" in step_two
    assert "`qos`" in step_two


def _leading_comment(path: Path) -> str:
    lines: list[str] = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_jobs_file_starts_with_the_setup_block(path: Path) -> None:
    header = _leading_comment(path)

    assert "configs.leonardo.yaml" in header
    assert "set-cluster-name leonardo" in header
    assert "sbatchman init" in header
    for removed in ("configs.yaml", "--configs-file", "--nodes "):
        assert removed not in header


def test_blink_noise_is_one_four_node_incast_job() -> None:
    doc = _load(EXAMPLES / "blink-noise.yaml")
    job = doc["jobs"][0]
    combinations = _combinations(doc)

    assert doc["variables"] == {"nodes": [4], "msgsize": [1048576]}
    assert len(combinations) == 1
    assert "{nodes}" in job["tag"]
    assert "{msgsize}" in job["tag"]
    # SbatchMan skips a job identical to one that ran: the tag must not match a sweep job's tag.
    sweep_tags = {
        _tag(sweep_job, values)
        for path in SWEEPS
        for sweep_job in _jobs(path)
        for values in _combinations(_load(path))
    }
    assert _tag(job, combinations[0]) not in sweep_tags
    aggressor = _config(job, combinations[0])["experiments"]["r1_noise"]["apps"]["1"]
    assert aggressor["path"] == "blink/incast/incast_nb.py"


@pytest.mark.parametrize("path", NOISE, ids=lambda p: p.name)
def test_the_noise_aggressor_runs_continuously(path: Path) -> None:
    doc = _load(path)
    for values in _combinations(doc):
        experiments = _config(doc["jobs"][0], values)["experiments"]
        for name, experiment in experiments.items():
            if not name.endswith("noise"):
                continue
            args = experiment["apps"]["1"]["args"].split()

            assert "-endl" in args, name
            assert "-blength" not in args, name
            assert "-bpause" not in args, name


def test_the_smoke_file_stays_one_job_on_two_nodes() -> None:
    doc = _load(EXAMPLES / "smoke.yaml")

    assert doc["variables"] == {"nodes": [2]}
    assert len(doc["jobs"]) == 1


@pytest.mark.parametrize(("path", "job", "values"), _cases())
def test_every_embedded_config_passes_the_checks(
    path: Path, job: dict[str, Any], values: dict[str, Any]
) -> None:
    config = _config(job, values)

    errors, _warnings = check_config_problems(config)

    assert errors == []
    assert config["global_options"]["name"] == _tag(job, values)
    assert config["global_options"]["numnodes"] == str(values["nodes"])


@pytest.mark.parametrize(("path", "job", "values"), _cases())
def test_no_variable_placeholder_is_left_after_substitution(
    path: Path, job: dict[str, Any], values: dict[str, Any]
) -> None:
    text = _config_text(job)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))

    assert not re.search(r"\{[a-z_]+\}", text)


@pytest.mark.parametrize("path", NOISE, ids=lambda p: p.name)
def test_the_experiments_alternate_baseline_and_noise_in_three_rounds(path: Path) -> None:
    """The engine runs experiments in sorted key order, so the written order is the run order."""
    doc = _load(path)
    for values in _combinations(doc):
        names = list(_config(doc["jobs"][0], values)["experiments"])

        assert names == EXPERIMENTS
        assert names == sorted(names)


@requires_wrappers_checkout
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_app_path_exists_in_the_wrappers_checkout(path: Path) -> None:
    doc = _load(path)
    values = _combinations(doc)[0]
    for job in doc["jobs"]:
        for experiment in _config(job, values)["experiments"].values():
            for app in experiment["apps"].values():
                assert wrapper_path_problem(app["path"]) is None


@pytest.mark.parametrize("path", NOISE, ids=lambda p: p.name)
def test_baseline_and_noise_share_the_job_the_allocation_and_the_victim(path: Path) -> None:
    """The fairness rule, inside one job: same nodes, same placement, same victim; only the
    aggressor differs."""
    doc = _load(path)
    for values in _combinations(doc):
        config = _config(doc["jobs"][0], values)
        experiments = config["experiments"]
        first = experiments[EXPERIMENTS[0]]
        allocation = first["local_options"]["allocation"]

        assert config["global_options"]["ppn"] == "4"
        assert allocation["mode"] == "interleaved"
        assert allocation["partitions"] == {"victim": {"share": 50}, "aggressor": {"share": 50}}
        for name, experiment in experiments.items():
            assert experiment["local_options"]["allocation"] == allocation, name
            assert experiment["apps"]["0"] == first["apps"]["0"], name
            assert list(experiment["apps"]) == (["0"] if name.endswith("baseline") else ["0", "1"])
        victim = first["apps"]["0"]
        assert victim["path"] == "blink/alltoall/alltoall_b.py"
        assert victim["partition"] == "victim"
        assert victim["collect"] is True
        for name in EXPERIMENTS:
            if name.endswith("noise"):
                aggressor = experiments[name]["apps"]["1"]
                assert aggressor["path"] == AGGRESSOR_PATH[path.name]
                assert aggressor["partition"] == "aggressor"
                assert aggressor["collect"] is False
                assert aggressor["end"] == "f"
                assert aggressor["args"] == "-msgsize 2097152 -endl"


@pytest.mark.parametrize("path", NOISE, ids=lambda p: p.name)
def test_the_victim_measures_the_swept_message_size_with_a_hundred_iterations(path: Path) -> None:
    doc = _load(path)
    for values in _combinations(doc):
        config = _config(doc["jobs"][0], values)
        for experiment in config["experiments"].values():
            args = experiment["apps"]["0"]["args"].split()

            assert args == ["-msgsize", str(values["msgsize"]), "-iter", "100"]


def test_the_smoke_victim_sets_a_message_size_and_iterations() -> None:
    doc = _load(EXAMPLES / "smoke.yaml")
    config = _config(doc["jobs"][0], _combinations(doc)[0])

    assert config["experiments"]["run"]["apps"]["0"]["args"] == "-msgsize 1048576 -iter 100"


def _qe_apps(path: Path) -> list[dict[str, Any]]:
    """Every QE app of the file's first variable assignment, read after the heredoc JSON."""
    doc = _load(path)
    config = _config(doc["jobs"][0], _combinations(doc)[0])
    return [
        app
        for experiment in config["experiments"].values()
        for app in experiment["apps"].values()
        if app["path"] == QE_WRAPPER
    ]


@pytest.mark.parametrize("name", QE_FILES)
def test_the_qe_files_read_the_input_from_the_checkout(name: str) -> None:
    path = EXAMPLES / name
    job = _jobs(path)[0]
    apps = _qe_apps(path)

    assert apps
    for app in apps:
        assert app["input_file"].startswith(QE_DIR)
        assert app["pseudo_dir"].startswith(QE_DIR)
        input_file = REPO / app["input_file"].replace("$CRAB_ROOT/", "")
        pseudo_dir = REPO / app["pseudo_dir"].replace("$CRAB_ROOT/", "")
        assert input_file == EXAMPLES / "qe" / "si-scf.in"
        assert input_file.is_file()
        upf_files = sorted(p.name for p in pseudo_dir.glob("*.UPF"))
        assert upf_files
        assert any(upf in input_file.read_text() for upf in upf_files)
    # The heredoc is unquoted so the shell expands $CRAB_ROOT: no other `$` or backtick may be in it.
    assert "<<JSON\n" in job["preprocess"]
    assert "<<'JSON'" not in job["preprocess"]
    assert "$CRAB_ROOT" in _config_text(job)
    assert not re.search(r"[$`]", _config_text(job).replace("$CRAB_ROOT", ""))


def test_qe_alone_is_one_two_node_job() -> None:
    doc = _load(EXAMPLES / "qe-alone.yaml")
    config = _config(doc["jobs"][0], _combinations(doc)[0])

    assert doc["variables"] == {"nodes": [2]}
    assert len(doc["jobs"]) == 1
    assert list(config["experiments"]) == ["run"]
    apps = config["experiments"]["run"]["apps"]
    assert list(apps) == ["0"]
    assert apps["0"]["path"] == QE_WRAPPER
    assert apps["0"]["collect"] is True


def test_qe_noise_runs_qe_against_a_continuous_incast() -> None:
    doc = _load(EXAMPLES / "qe-noise.yaml")
    config = _config(doc["jobs"][0], _combinations(doc)[0])
    experiments = config["experiments"]
    first = experiments[EXPERIMENTS[0]]
    allocation = first["local_options"]["allocation"]

    assert doc["variables"] == {"nodes": [4]}
    assert config["global_options"]["ppn"] == "4"
    assert list(experiments) == EXPERIMENTS
    assert allocation["mode"] == "interleaved"
    assert allocation["partitions"] == {"victim": {"share": 50}, "aggressor": {"share": 50}}
    for name, experiment in experiments.items():
        assert experiment["local_options"]["allocation"] == allocation, name
        assert experiment["apps"]["0"] == first["apps"]["0"], name
        assert list(experiment["apps"]) == (["0"] if name.endswith("baseline") else ["0", "1"])
    victim = first["apps"]["0"]
    assert victim["path"] == QE_WRAPPER
    assert victim["partition"] == "victim"
    assert victim["collect"] is True
    for name in EXPERIMENTS:
        if name.endswith("noise"):
            aggressor = experiments[name]["apps"]["1"]
            assert aggressor["path"] == "blink/incast/incast_nb.py"
            assert aggressor["partition"] == "aggressor"
            assert aggressor["collect"] is False
            assert aggressor["end"] == "f"
            assert aggressor["args"] == "-msgsize 2097152 -endl"
