"""The GPU comm-only suites generator (examples/nplb/gpu_set.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
H100 = NPLB / "h100"


def _load(name: str) -> ModuleType:
    """Import a script from examples/nplb (not a package) under its own file name."""
    if str(NPLB) not in sys.path:
        sys.path.insert(0, str(NPLB))
    spec = importlib.util.spec_from_file_location(name, NPLB / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gpu_set = _load("gpu_set")

SIZES = [
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
    268435456,
    536870912,
]
CORES = ["0", "1", "2", "3", "40", "41", "42", "43"]
NICS = [
    "mlx5_0:1",
    "mlx5_2:1",
    "mlx5_4:1",
    "mlx5_8:1",
    "mlx5_10:1",
    "mlx5_12:1",
    "mlx5_14:1",
    "mlx5_16:1",
]
GPU_ENV = {
    "UCX_MAX_RNDV_RAILS": "1",
    "UCX_IB_ROCE_REACHABILITY_MODE": "all",
    "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
}
BIN = "~/crab/lab/blink-gpu/build-cuda/bin"
A2A_WRAPPER = "blink/alltoall/alltoall_comm_only.py"
AG_WRAPPER = "blink/allgather/allgather_comm_only.py"
FULL_NAMES = [f"ppn{p}_{kind}" for p in (1, 2, 4, 8) for kind in ("alone", "vs_a2a")]
SMOKE_NAMES = [f"ppn{p}_{kind}" for p in (1, 8) for kind in ("alone", "vs_a2a")]
STEMS = {"a2a": "gpu_a2a_comm", "ag": "gpu_ag_comm"}
VICTIMS = {
    "a2a": (A2A_WRAPPER, "alltoall_comm_only"),
    "ag": (AG_WRAPPER, "allgather_comm_only"),
}


def generate(tmp_path: Path, *args: str) -> dict[str, dict[str, Any]]:
    """Run the generator into tmp_path; return {file name: parsed config}."""
    assert gpu_set.main(["--out", str(tmp_path), *args]) == 0
    return {p.name: json.loads(p.read_text()) for p in sorted(tmp_path.glob("*.json"))}


def app_list(config: dict[str, Any], exp: str) -> list[dict[str, Any]]:
    apps = config["experiments"][exp]["apps"]
    return [apps[k] for k in sorted(apps, key=int)]


def victim_apps(config: dict[str, Any], exp: str) -> list[dict[str, Any]]:
    """The apps of an experiment without the aggressor (the last app of a `vs` one)."""
    apps = app_list(config, exp)
    return apps[:-1] if "_vs_" in exp else apps


def option(app: dict[str, Any], flag: str) -> str:
    words = app["args"].split()
    return words[words.index(flag) + 1]


def sizes_of(apps: list[dict[str, Any]]) -> list[int]:
    return [int(option(app, "-msgsize")) for app in apps]


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return generate(tmp_path_factory.mktemp("full"))


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return generate(tmp_path_factory.mktemp("smoke"), "--smoke")


@pytest.fixture(scope="module")
def local(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return generate(tmp_path_factory.mktemp("local"), "--local")


def test_file_names(full: dict[str, Any], smoke: dict[str, Any], local: dict[str, Any]) -> None:
    assert sorted(full) == ["gpu_a2a_comm.json", "gpu_ag_comm.json"]
    assert sorted(smoke) == ["smoke_gpu_a2a_comm.json", "smoke_gpu_ag_comm.json"]
    assert sorted(local) == ["local_gpu_a2a_comm.json", "local_gpu_ag_comm.json"]


def test_smoke_and_local_together_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gpu_set.main(["--out", str(tmp_path), "--smoke", "--local"]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert list(tmp_path.iterdir()) == []


def test_experiment_names(full: dict[str, Any], smoke: dict[str, Any]) -> None:
    for config in full.values():
        assert list(config["experiments"]) == FULL_NAMES
    for config in smoke.values():
        assert list(config["experiments"]) == SMOKE_NAMES


def test_sizes_and_allgather_divisibility(full: dict[str, Any], smoke: dict[str, Any]) -> None:
    for config in full.values():
        for exp in config["experiments"]:
            assert sizes_of(victim_apps(config, exp)) == SIZES, exp
    for config in smoke.values():
        for exp in config["experiments"]:
            apps = victim_apps(config, exp)
            assert sizes_of(apps) == [1048576, 67108864], exp
            assert {option(a, "-iter") for a in apps} == {"20"}, exp
    for config in (full["gpu_ag_comm.json"], smoke["smoke_gpu_ag_comm.json"]):
        for exp in config["experiments"]:
            assert all(size % 4 == 0 for size in sizes_of(victim_apps(config, exp)))


def test_chain_structure(full: dict[str, Any]) -> None:
    for config in full.values():
        for exp in config["experiments"]:
            apps = victim_apps(config, exp)
            head = apps[0]
            assert head["start"] == ("3" if "_vs_" in exp else "0"), exp
            assert head["partition"] == "victim"
            assert "cpus" in head and "nics" in head
            for k, app in enumerate(apps[1:]):
                assert app["start"] == f"s{k}", exp
                assert "cpus" not in app and "partition" not in app, exp
            keys = list(config["experiments"][exp]["apps"])
            assert keys == [str(i) for i in range(len(keys))], exp


def test_pinning_is_the_first_p_of_the_h100_layout(full: dict[str, Any]) -> None:
    for config in full.values():
        for exp in config["experiments"]:
            p = int(exp.split("_")[0].removeprefix("ppn"))
            apps = victim_apps(config, exp)
            assert apps[0]["cpus"] == ",".join(CORES[:p]), exp
            for app in apps:
                assert app["ppn"] == p, exp
                assert app["nics"] == NICS[:p], exp  # chained apps carry the list too


def test_binary_args_env_and_wrapper(full: dict[str, Any]) -> None:
    for key, stem in STEMS.items():
        wrapper, program = VICTIMS[key]
        config = full[f"{stem}.json"]
        for exp in config["experiments"]:
            apps = app_list(config, exp)
            for app in apps:
                is_aggressor = "_vs_" in exp and app is apps[-1]
                want_wrapper, want_program = (A2A_WRAPPER, "alltoall_comm_only")
                if not is_aggressor:
                    want_wrapper, want_program = wrapper, program
                assert app["path"] == want_wrapper, exp
                assert app["binary"] == f"{BIN}/{want_program}", exp
                assert "-gpu" in app["args"].split(), exp
                assert app["env"] == GPU_ENV, exp
                assert option(app, "-grty") == "1", exp
                words = app["args"].split()
                assert "-offset" not in words and "-maxsamples" not in words, exp
    for config in full.values():
        blob = json.dumps(config)
        assert "UCX_IB_NUM_PATHS" not in blob
        assert "UCX_LOG_LEVEL" not in blob


def test_iterations(full: dict[str, Any]) -> None:
    for config in full.values():
        for exp in config["experiments"]:
            for app in victim_apps(config, exp):
                size = int(option(app, "-msgsize"))
                if size < 32 * 1024 * 1024:
                    assert (option(app, "-iter"), option(app, "-warmup")) == ("1000", "100")
                else:
                    assert (option(app, "-iter"), option(app, "-warmup")) == ("100", "50")


def test_aggressor(full: dict[str, Any]) -> None:
    for config in full.values():
        for exp in config["experiments"]:
            apps = app_list(config, exp)
            if exp.endswith("_alone"):
                assert all(a["partition"] == "victim" for a in apps[:1])
                assert all(a.get("partition") != "aggressor" for a in apps), exp
                continue
            p = int(exp.split("_")[0].removeprefix("ppn"))
            agg = apps[-1]
            assert set(agg["args"].split()) >= {"-msgsize", "2097152", "-endl", "-gpu"}
            assert option(agg, "-msgsize") == "2097152"
            assert "-blength" not in agg["args"].split()
            assert agg["collect"] is False
            assert (agg["start"], agg["end"]) == ("0", "f")
            assert agg["partition"] == "aggressor"
            assert agg["ppn"] == p
            assert agg["cpus"] == ",".join(CORES[:p])
            assert agg["nics"] == NICS[:p]
            keys = list(config["experiments"][exp]["apps"])
            assert keys[-1] == str(len(SIZES))


def test_global_options(full: dict[str, Any], smoke: dict[str, Any]) -> None:
    for stem_key, stem in STEMS.items():
        for prefix, built, walltime, timeout in (
            ("", full, "06:00:00", "3600.0"),
            ("smoke_", smoke, "00:30:00", "600.0"),
        ):
            name = f"{prefix}{stem}"
            config = built[f"{name}.json"]
            opts = config["global_options"]
            assert config["schema_version"] == 2
            assert opts["name"] == name, stem_key
            assert opts["numnodes"] == "4" and opts["ppn"] == "8"
            assert opts["minruns"] == "1" and opts["maxruns"] == "1"
            assert opts["outformat"] == "csv"
            assert opts["walltime"] == walltime and opts["timeout"] == timeout
            assert "--exclusive" in opts["sbatch_directives"]
            assert f"--job-name=nplb_{name}" in opts["sbatch_directives"]
            assert opts["allocation"] == {
                "mode": "interleaved",
                "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}},
            }
            assert "--nodelist" not in json.dumps(config)
            assert "node1" not in json.dumps(config)


def test_aggressor_ppn_and_size_options(tmp_path: Path) -> None:
    out = generate(tmp_path, "--aggr-ppn", "8", "--aggr-msgsize", "16777216")
    config = out["gpu_a2a_comm.json"]
    assert list(config["experiments"]) == FULL_NAMES
    for exp in config["experiments"]:
        if exp.endswith("_alone"):
            continue
        agg = app_list(config, exp)[-1]
        assert agg["ppn"] == 8
        assert agg["cpus"] == ",".join(CORES)
        assert agg["nics"] == NICS
        assert option(agg, "-msgsize") == "16777216"


def test_aggressor_sweep_names(tmp_path: Path) -> None:
    out = generate(tmp_path, "--aggr-ppn", "4,8", "--aggr-msgsize", "2097152,16777216")
    config = out["gpu_ag_comm.json"]
    for p in (1, 2, 4, 8):
        expected = [f"ppn{p}_alone"] + [
            f"ppn{p}_vs_a2a_ap{ap}_as{size}" for ap in (4, 8) for size in (2097152, 16777216)
        ]
        names = [n for n in config["experiments"] if n.startswith(f"ppn{p}_")]
        assert names == expected
    agg = app_list(config, "ppn2_vs_a2a_ap8_as16777216")[-1]
    assert agg["ppn"] == 8 and option(agg, "-msgsize") == "16777216"
    agg = app_list(config, "ppn2_vs_a2a_ap4_as2097152")[-1]
    assert agg["ppn"] == 4 and option(agg, "-msgsize") == "2097152"


def test_one_value_in_a_list_keeps_the_plain_name_and_same_follows_the_victim(
    tmp_path: Path,
) -> None:
    config = generate(tmp_path, "--aggr-ppn", "same,same")["gpu_a2a_comm.json"]
    assert list(config["experiments"]) == FULL_NAMES
    assert app_list(config, "ppn2_vs_a2a")[-1]["ppn"] == 2


def test_two_aggressor_ppn_values_name_every_experiment_with_ap_and_as(tmp_path: Path) -> None:
    config = generate(tmp_path, "--aggr-ppn", "1,2")["gpu_a2a_comm.json"]
    assert "ppn4_vs_a2a_ap1_as2097152" in config["experiments"]
    assert "ppn4_vs_a2a_ap2_as2097152" in config["experiments"]
    assert "ppn4_vs_a2a" not in config["experiments"]


@pytest.mark.parametrize(
    "args",
    [
        ["--aggr-ppn", "3"],
        ["--aggr-ppn", "many"],
        ["--aggr-ppn", "4,,8"],
        ["--aggr-msgsize", "abc"],
        ["--aggr-msgsize", "0"],
        ["--aggr-msgsize", "2097152,-4"],
    ],
)
def test_bad_aggressor_values_exit_2(
    args: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gpu_set.main(["--out", str(tmp_path), *args]) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert list(tmp_path.iterdir()) == []


def test_local_variant(local: dict[str, Any]) -> None:
    for config in local.values():
        text = json.dumps(config)
        for word in ('"binary"', "-gpu", '"nics"', '"cpus"', "CUDA_DEVICE_ORDER", "UCX_"):
            assert word not in text, word
        names = list(config["experiments"])
        assert names == [f"ppn{p}_{k}" for p in (1, 2) for k in ("alone", "vs_a2a")]
        opts = config["global_options"]
        assert opts["numnodes"] == "4"
        assert opts["allocation"]["mode"] == "interleaved"
        assert {k: v["share"] for k, v in opts["allocation"]["partitions"].items()} == {
            "victim": 50,
            "aggressor": 50,
        }
        for exp in names:
            for app in victim_apps(config, exp):
                assert (option(app, "-iter"), option(app, "-warmup")) == ("5", "1")
            assert sizes_of(victim_apps(config, exp)) == [1024, 1048576]
        assert app_list(config, "ppn1_vs_a2a")[-1]["path"] == A2A_WRAPPER
    assert app_list(local["local_gpu_ag_comm.json"], "ppn1_alone")[0]["path"] == AG_WRAPPER
    assert app_list(local["local_gpu_a2a_comm.json"], "ppn1_alone")[0]["path"] == A2A_WRAPPER


def test_every_generated_file_passes_the_checks(
    full: dict[str, Any], smoke: dict[str, Any], local: dict[str, Any]
) -> None:
    from crab.core.config_checks import check_config
    from settings_fixtures import slurm_settings

    settings = slurm_settings()
    for built in (full, smoke, local):  # the local files are checked as Slurm jobs too
        for name, config in built.items():
            assert check_config(config, settings) == [], name


@pytest.mark.parametrize("prefix", ["", "smoke_"])
def test_committed_files_are_what_the_generator_writes_now(prefix: str, tmp_path: Path) -> None:
    out = generate(tmp_path, *(["--smoke"] if prefix else []))
    assert out
    for name in out:
        committed = (H100 / name).read_text()
        assert committed == (tmp_path / name).read_text(), f"{name} is stale: rerun gpu_set.py"
