"""The NPLB campaign config generator (examples/nplb/make_configs.py) and its naming module."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crab.core.allocation.allocator import NodeAllocator

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"


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


make_configs = _load("make_configs")
naming = _load("naming")

NODE_NAMES = ["node-a", "node-b", "node-c", "node-d"]
MD_SIZES = [1, 8, 64, 512, 4096, 32768, 262144, 2097152, 16777216, 134217728, 536870912]
COL_SIZES = [8, 64, 512, 4096, 32768, 262144, 2097152, 16777216]
SMOKE_SIZES = [8, 32768, 2097152]
BURSTS = [(l_, p) for l_ in ("0.001", "0.01", "0.1") for p in ("0.000001", "0.0001", "0.01")]


def profile(nodes: int = 4, ppn: int = 8, **extra: Any) -> dict[str, Any]:
    cores = [0, 1, 2, 3, 32, 33, 34, 35] if ppn == 8 else [16 * i for i in range(ppn)]
    return {
        "nodes": NODE_NAMES[:nodes] if nodes <= 4 else [f"n{i}" for i in range(nodes)],
        "ppn": ppn,
        "nics": [f"mlx5_{i}:1" for i in range(ppn)],
        "cpus": ",".join(str(c) for c in cores),
        "mem_gib": 1800,
        "preset_hint": "h100",
        **extra,
    }


def generate(tmp_path: Path, prof: dict[str, Any], *args: str) -> dict[str, dict[str, Any]]:
    """Run the generator on a profile file; return {file name: parsed config}."""
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(prof))
    out = tmp_path / "out"
    assert make_configs.main([str(prof_path), "--out", str(out), *args]) == 0
    return {p.name: json.loads(p.read_text()) for p in sorted(out.glob("*.json"))}


def slurm_settings() -> Any:
    from settings_fixtures import slurm_settings as make

    return make()


class FakeApp:
    """What NodeAllocator.allocate_experiment reads and writes on an app."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.start_string = str(cfg.get("start", "0"))
        self.partition_id = cfg.get("partition") or None
        self.nodes: list[str] = []

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)


def place(config: dict[str, Any], exp: str, node_names: list[str]) -> dict[str, list[str]]:
    """The nodes of each app of one experiment, from CRAB's own allocator."""
    experiment = config["experiments"][exp]
    opts = {**config["global_options"], **experiment.get("local_options", {})}
    keys = sorted(experiment["apps"], key=int)
    apps = [FakeApp(experiment["apps"][k]) for k in keys]
    NodeAllocator.allocate_experiment(apps, node_names, opts["allocation"])
    return {k: a.nodes for k, a in zip(keys, apps, strict=True)}


def app_list(config: dict[str, Any], exp: str) -> list[dict[str, Any]]:
    apps = config["experiments"][exp]["apps"]
    return [apps[k] for k in sorted(apps, key=int)]


def sizes_of(apps: list[dict[str, Any]]) -> list[int]:
    out = []
    for app in apps:
        words = app["args"].split()
        out.append(int(words[words.index("-msgsize") + 1]))
    return out


def iter_of(app: dict[str, Any]) -> int:
    words = app["args"].split()
    return int(words[words.index("-iter") + 1])


@pytest.fixture(scope="module")
def four_node(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return generate(tmp_path_factory.mktemp("four"), profile(4, 8))


@pytest.fixture(scope="module")
def three_node(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return generate(tmp_path_factory.mktemp("three"), profile(3, 8), "--set", "md")


def test_four_node_file_list_and_every_config_passes_the_checks(
    four_node: dict[str, dict[str, Any]],
) -> None:
    from crab.core.config_checks import check_config

    expected = set()
    for net, paths_list in (("C0", (1, 4)), ("C2", (1, 4)), ("C3", (1,))):
        for p in paths_list:
            expected.add(f"md_p1_{net}_P{p}.json")
            if net != "C3":
                expected |= {f"md_p0_{net}_P{p}.json"}
                expected |= {f"md_p2_{v}_{net}_P{p}.json" for v in ("perm", "a2a", "ag")}
            if net != "C3":  # C3 is the md's phase-1 check only: no colleague files
                expected |= {f"col_{s}_{net}_P{p}.json" for s in ("a2a", "agtr", "ardc")}
    assert set(four_node) == expected
    settings = slurm_settings()
    for name, config in four_node.items():
        assert config["schema_version"] == 2, name
        assert check_config(config, settings) == [], name


def test_global_options(four_node: dict[str, dict[str, Any]]) -> None:
    opts = four_node["md_p2_perm_C0_P1.json"]["global_options"]
    assert opts["numnodes"] == "4"
    assert opts["ppn"] == "8"
    assert opts["outformat"] == "csv"
    assert opts["sbatch_directives"] == ["--exclusive", "--job-name=nplb_md_p2_perm_C0_P1"]
    assert opts["timeout"] == "3600.0"
    assert opts["walltime"] == "04:00:00"


def test_four_node_interleaved_partitions_put_victim_on_nodes_1_and_3(
    four_node: dict[str, dict[str, Any]],
) -> None:
    nodes = ["n1", "n2", "n3", "n4"]
    config = four_node["md_p2_a2a_C2_P1.json"]
    for exp in config["experiments"]:
        placed = place(config, exp, nodes)
        keys = sorted(placed, key=int)
        for key in keys[:-1] if "_vs_" in exp else keys:
            assert placed[key] == ["n1", "n3"], (exp, key)
        if "_vs_" in exp:
            assert placed[keys[-1]] == ["n2", "n4"], exp
    p0 = four_node["md_p0_C0_P1.json"]
    assert set(map(tuple, place(p0, "p0_cross_C0_P1", nodes).values())) == {("n1", "n3")}
    local = place(p0, "p0_local_C0_P1", nodes)
    assert set(map(tuple, local.values())) == {("n1", "n2")}
    p1 = four_node["md_p1_C0_P1.json"]
    for exp in p1["experiments"]:
        assert set(map(tuple, place(p1, exp, nodes).values())) == {tuple(nodes)}


def test_three_node_layout(three_node: dict[str, dict[str, Any]]) -> None:
    from crab.core.config_checks import check_config

    nodes = ["n1", "n2", "n3"]
    for config in three_node.values():
        assert check_config(config, slurm_settings()) == []
        assert config["global_options"]["numnodes"] == "3"
    p2 = three_node["md_p2_perm_C0_P1.json"]
    assert set(p2["experiments"]) == {"p2_perm_alone_C0_P1", "p2_perm_vs_pw_C0_P1"}
    vs = "p2_perm_vs_pw_C0_P1"
    placed = place(p2, vs, nodes)
    aggressor_key = max(placed, key=int)
    assert all(v == ["n1", "n3"] for k, v in placed.items() if k != aggressor_key)
    assert placed[aggressor_key] == ["n2"]
    aggressor = app_list(p2, vs)[-1]
    assert aggressor["path"] == "blink/pairwise_nb.py"
    assert aggressor["args"] == "-mode rot -offset 4 -msgsize 2097152 -endl"
    assert aggressor["env"]["UCX_TLS"] == "rc,self"
    victim = app_list(p2, vs)[0]
    assert victim["args"].startswith("-mode rot -offset 8 ")
    assert "UCX_TLS" not in victim["env"]
    p1 = three_node["md_p1_C0_P1.json"]
    perm = app_list(p1, "p1_perm_C0_P1")
    assert perm[0]["args"].startswith("-mode rot -offset 8 -msgsize 1 ")
    assert perm[0]["partition"] == "victim"
    assert set(map(tuple, place(p1, "p1_perm_C0_P1", nodes).values())) == {("n1", "n3")}
    for exp in ("p1_a2a_C0_P1", "p1_ag_C0_P1"):
        assert set(map(tuple, place(p1, exp, nodes).values())) == {tuple(nodes)}


def test_chains_and_start_values(four_node: dict[str, dict[str, Any]]) -> None:
    config = four_node["md_p2_ag_C0_P4.json"]
    vs = app_list(config, "p2_ag_vs_a2a_C0_P4")
    chain, aggressor = vs[:-1], vs[-1]
    assert len(chain) == len(MD_SIZES)
    assert chain[0]["start"] == "3"
    assert [a["start"] for a in chain[1:]] == [f"s{k}" for k in range(len(chain) - 1)]
    assert [a.get("partition") for a in chain] == ["victim"] + [None] * (len(chain) - 1)
    assert all(a["collect"] is True and a["end"] == "" for a in chain)
    assert aggressor["partition"] == "aggressor"
    assert aggressor["start"] == "0" and aggressor["end"] == "f"
    assert aggressor["collect"] is False
    assert aggressor["path"] == "blink/alltoall_nb.py"
    assert aggressor["args"] == "-msgsize 2097152 -endl"
    alone = app_list(config, "p2_ag_alone_C0_P4")
    assert len(alone) == len(MD_SIZES)
    assert alone[0]["start"] == "0"
    # a2a, nine bursts, incast, alone: 12 experiments
    assert len(config["experiments"]) == 12
    burst = app_list(config, "p2_ag_vs_burst-0.01-0.0001_C0_P4")[-1]
    assert burst["args"] == "-msgsize 2097152 -endl -blength 0.01 -bpause 0.0001"
    incast = app_list(config, "p2_ag_vs_incast_C0_P4")[-1]
    assert incast["path"] == "blink/incast_nb.py"
    assert incast["args"] == "-msgsize 2097152 -endl"


def test_victim_programs_and_offsets(four_node: dict[str, dict[str, Any]]) -> None:
    perm = app_list(four_node["md_p2_perm_C0_P1.json"], "p2_perm_alone_C0_P1")[0]
    assert perm["path"] == "blink/pairwise_nb.py"
    assert perm["args"].startswith("-mode rot -offset 8 -msgsize 1 -iter 1000")
    a2a = app_list(four_node["md_p2_a2a_C0_P1.json"], "p2_a2a_alone_C0_P1")[0]
    assert a2a["path"] == "blink/alltoall_nb.py"
    ag = app_list(four_node["md_p2_ag_C0_P1.json"], "p2_ag_alone_C0_P1")[0]
    assert ag["path"] == "blink/allgather_nb.py"
    p1 = four_node["md_p1_C0_P1.json"]
    assert app_list(p1, "p1_perm_C0_P1")[0]["args"].startswith("-mode rot -offset 16 -msgsize 1 ")
    assert [a["path"] for a in (app_list(p1, e)[0] for e in p1["experiments"])] == [
        "blink/pairwise_nb.py",
        "blink/alltoall_nb.py",
        "blink/allgather_nb.py",
    ]
    for exp in p1["experiments"]:
        chain = app_list(p1, exp)
        assert sizes_of(chain) == MD_SIZES
        assert [a.get("partition") for a in chain] == [None] * len(MD_SIZES)
        assert [a["start"] for a in chain] == ["0"] + [f"s{k}" for k in range(10)]


def test_phase_0(four_node: dict[str, dict[str, Any]]) -> None:
    config = four_node["md_p0_C0_P1.json"]
    assert list(config["experiments"]) == ["p0_cross_C0_P1", "p0_local_C0_P1"]
    for exp in config["experiments"]:
        apps = app_list(config, exp)
        assert sizes_of(apps) == MD_SIZES
        for app in apps:
            assert app["path"] == "blink/pingpong_b.py"
            assert app["ppn"] == 1
            assert app["nics"] == ["mlx5_0:1"]
            assert app.get("cpus") == (None if app["start"].startswith("s") else "0")
    cross = config["experiments"]["p0_cross_C0_P1"]["local_options"]["allocation"]
    assert cross["mode"] == "interleaved"
    assert cross["partitions"] == {"victim": {"share": 50}, "aggressor": {"share": 50}}
    local = config["experiments"]["p0_local_C0_P1"]["local_options"]["allocation"]
    assert local["mode"] == "linear"
    assert [a["partition"] for a in app_list(config, "p0_local_C0_P1") if "partition" in a] == [
        "victim"
    ]


def test_three_node_phase_0_shares(three_node: dict[str, dict[str, Any]]) -> None:
    config = three_node["md_p0_C0_P1.json"]
    nodes = ["n1", "n2", "n3"]
    assert set(map(tuple, place(config, "p0_cross_C0_P1", nodes).values())) == {("n1", "n3")}
    assert set(map(tuple, place(config, "p0_local_C0_P1", nodes).values())) == {("n1", "n2")}
    shares = config["experiments"]["p0_cross_C0_P1"]["local_options"]["allocation"]["partitions"]
    assert shares == {"victim": {"share": 67}, "aggressor": {"share": 33}}


def test_iterations_per_size(four_node: dict[str, dict[str, Any]]) -> None:
    apps = app_list(four_node["md_p2_perm_C0_P1.json"], "p2_perm_alone_C0_P1")
    by_size = {s: iter_of(a) for s, a in zip(sizes_of(apps), apps, strict=True)}
    assert sizes_of(apps) == MD_SIZES
    assert {s: i for s, i in by_size.items() if s <= 16777216} == dict.fromkeys(MD_SIZES[:9], 1000)
    assert by_size[134217728] == 100
    assert by_size[536870912] == 25


def test_iteration_flags(tmp_path: Path) -> None:
    configs = generate(
        tmp_path,
        profile(),
        "--set",
        "md",
        "--net",
        "C0",
        "--paths",
        "1",
        "--iter-small",
        "7",
        "--iter-128m",
        "5",
        "--iter-512m",
        "3",
    )
    apps = app_list(configs["md_p2_perm_C0_P1.json"], "p2_perm_alone_C0_P1")
    assert [iter_of(a) for a in apps] == [7] * 9 + [5, 3]


def test_memory_skip_is_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configs = generate(
        tmp_path, profile(), "--set", "md", "--net", "C0", "--paths", "1", "--mem-gib", "16"
    )
    config = configs["md_p2_ag_C0_P1.json"]
    exp = config["experiments"]["p2_ag_alone_C0_P1"]
    sizes = sizes_of(app_list(config, "p2_ag_alone_C0_P1"))
    assert 536870912 not in sizes
    assert 134217728 not in sizes
    assert 32768 in sizes
    assert "536870912" in exp["description"] and "skipped" in exp["description"].lower()
    summary = capsys.readouterr().out
    assert "p2_ag_alone_C0_P1" in summary and "536870912" in summary
    # the chain stays valid after the skip: sizes still chained one after the other
    apps = app_list(config, "p2_ag_alone_C0_P1")
    assert [a["start"] for a in apps[1:]] == [f"s{k}" for k in range(len(apps) - 1)]
    # ping-pong needs only 2x the size per rank: it is kept at 16 GiB per node
    p0 = app_list(configs["md_p0_C0_P1.json"], "p0_cross_C0_P1")
    assert sizes_of(p0) == MD_SIZES


def test_default_memory_keeps_the_largest_sizes(four_node: dict[str, dict[str, Any]]) -> None:
    for name in ("md_p2_a2a_C0_P1.json", "md_p2_ag_C0_P1.json", "md_p2_perm_C0_P1.json"):
        config = four_node[name]
        exp = next(iter(config["experiments"]))
        assert sizes_of(app_list(config, exp)) == MD_SIZES


def test_mem_gib_comes_from_the_profile(tmp_path: Path) -> None:
    prof = profile(mem_gib=16)
    configs = generate(tmp_path, prof, "--set", "md", "--net", "C0", "--paths", "1")
    sizes = sizes_of(app_list(configs["md_p2_ag_C0_P1.json"], "p2_ag_alone_C0_P1"))
    assert 536870912 not in sizes


def test_runs_per_network(four_node: dict[str, dict[str, Any]]) -> None:
    c0 = four_node["md_p2_perm_C0_P1.json"]["global_options"]
    c2 = four_node["md_p2_perm_C2_P4.json"]["global_options"]
    c3 = four_node["md_p1_C3_P1.json"]["global_options"]
    assert (c0["minruns"], c0["maxruns"]) == ("20", "40")
    assert (c2["minruns"], c2["maxruns"]) == ("10", "20")
    assert (c3["minruns"], c3["maxruns"]) == ("10", "20")


def test_run_flags_override(tmp_path: Path) -> None:
    configs = generate(
        tmp_path,
        profile(),
        "--net",
        "C0",
        "--paths",
        "1",
        "--minruns",
        "3",
        "--maxruns",
        "9",
        "--walltime",
        "01:00:00",
        "--timeout",
        "99",
    )
    for config in configs.values():
        opts = config["global_options"]
        assert (opts["minruns"], opts["maxruns"]) == ("3", "9")
        assert opts["walltime"] == "01:00:00"
        assert opts["timeout"] == "99.0"


def test_colleague_set(four_node: dict[str, dict[str, Any]]) -> None:
    programs = {
        "a2a": "blink/alltoall_comm_only.py",
        "agtr": "blink/allgather_comm_only.py",
        "ardc": "blink/allreduce_nb.py",
    }
    for stem, program in programs.items():
        config = four_node[f"col_{stem}_C2_P4.json"]
        opts = config["global_options"]
        assert (opts["minruns"], opts["maxruns"], opts["timeout"]) == ("1", "1", "1200.0")
        assert opts["walltime"] == "00:20:00"
        assert opts["allocation"] == {
            "mode": "interleaved",
            "partitions": {"victim": {"share": 50}, "aggressor": {"share": 50}},
        }
        expected = [f"cong_burst-{b}-{p}_C2_P4" for b, p in BURSTS]
        assert list(config["experiments"]) == expected
        for (b, p), exp in zip(BURSTS, expected, strict=True):
            apps = app_list(config, exp)
            chain, aggressor = apps[:-1], apps[-1]
            assert sizes_of(chain) == COL_SIZES
            assert all(a["path"] == program and iter_of(a) == 1000 for a in chain)
            assert chain[0]["start"] == "0" and chain[0]["partition"] == "victim"
            assert [a["start"] for a in chain[1:]] == [f"s{k}" for k in range(7)]
            assert aggressor["path"] == "blink/alltoall_man.py"
            assert aggressor["args"] == f"-endl -msgsize 2097152 -blength {b} -bpause {p}"
            assert aggressor["partition"] == "aggressor"
            assert aggressor["start"] == "0" and aggressor["end"] == "f"
            assert aggressor["collect"] is False
            assert "UCX_TLS" not in aggressor["env"]
            assert config["experiments"][exp]["description"].strip()


def test_colleague_set_on_three_nodes(tmp_path: Path) -> None:
    configs = generate(tmp_path, profile(3), "--set", "colleague", "--net", "C0", "--paths", "1")
    config = configs["col_a2a_C0_P1.json"]
    shares = config["global_options"]["allocation"]["partitions"]
    assert shares == {"victim": {"share": 67}, "aggressor": {"share": 33}}
    placed = place(config, "cong_burst-0.01-0.0001_C0_P1", ["n1", "n2", "n3"])
    assert placed["0"] == ["n1", "n3"] and placed["8"] == ["n2"]


def test_smoke(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config

    configs = generate(tmp_path, profile(4, 4), "--smoke", "--net", "C0", "--paths", "1")
    assert set(configs) == {
        "smoke_md_p0_C0_P1.json",
        "smoke_md_p1_C0_P1.json",
        "smoke_md_p2_perm_C0_P1.json",
        "smoke_md_p2_a2a_C0_P1.json",
        "smoke_md_p2_ag_C0_P1.json",
        "smoke_col_a2a_C0_P1.json",
        "smoke_col_agtr_C0_P1.json",
        "smoke_col_ardc_C0_P1.json",
    }
    for name, config in configs.items():
        assert check_config(config, slurm_settings()) == [], name
        opts = config["global_options"]
        assert (opts["minruns"], opts["maxruns"]) == ("1", "1")
        assert opts["timeout"] == "300.0" and opts["walltime"] == "00:15:00"
        assert opts["ppn"] == "4"
        assert opts["sbatch_directives"][1] == f"--job-name=nplb_{name[: -len('.json')]}"
        for exp in config["experiments"].values():
            chain = [a for a in exp["apps"].values() if a["collect"]]
            assert sizes_of(chain) == SMOKE_SIZES
    p2 = configs["smoke_md_p2_a2a_C0_P1.json"]
    assert list(p2["experiments"]) == [
        "p2_a2a_alone_C0_P1",
        "p2_a2a_vs_a2a_C0_P1",
        "p2_a2a_vs_burst-0.01-0.0001_C0_P1",
        "p2_a2a_vs_incast_C0_P1",
    ]
    for exp in p2["experiments"]:
        apps = app_list(p2, exp)
        victims = [a for a in apps if a["collect"]]
        assert sizes_of(victims) == SMOKE_SIZES
        assert all(iter_of(a) == 50 for a in victims)
    col = configs["smoke_col_a2a_C0_P1.json"]
    assert list(col["experiments"]) == ["cong_burst-0.01-0.0001_C0_P1"]
    chain = app_list(col, "cong_burst-0.01-0.0001_C0_P1")[:-1]
    assert sizes_of(chain) == SMOKE_SIZES and all(iter_of(a) == 50 for a in chain)
    p0 = configs["smoke_md_p0_C0_P1.json"]
    assert sizes_of(app_list(p0, "p0_cross_C0_P1")) == SMOKE_SIZES
    assert app_list(p2, "p2_a2a_alone_C0_P1")[0]["ppn"] == 4


def test_smoke_with_more_than_four_nodes_uses_the_four_node_layout(tmp_path: Path) -> None:
    configs = generate(
        tmp_path, profile(6, 4), "--smoke", "--set", "md", "--net", "C0", "--paths", "1"
    )
    assert configs["smoke_md_p1_C0_P1.json"]["global_options"]["numnodes"] == "4"


def test_every_name_round_trips(four_node: dict[str, dict[str, Any]]) -> None:
    seen = 0
    for file_name, config in four_node.items():
        for exp in config["experiments"]:
            parsed = naming.parse_experiment_name(exp)
            assert parsed is not None, exp
            assert (
                naming.experiment_name(
                    parsed["phase"], parsed["test"], parsed["net"], parsed["paths"]
                )
                == exp
            )
            assert exp.endswith(f"_{parsed['net']}_P{parsed['paths']}")
            assert f"_{parsed['net']}_P{parsed['paths']}.json" in file_name
            seen += 1
    assert seen == 275  # 8 p0 + 15 p1 + 144 p2 + 108 colleague


def test_parse_experiment_name_fields() -> None:
    parsed = naming.parse_experiment_name("p2_perm_vs_burst-0.01-0.0001_C2_P1")
    assert parsed == {
        "phase": "p2",
        "test": "perm_vs_burst-0.01-0.0001",
        "victim": "perm",
        "aggressor": "burst-0.01-0.0001",
        "net": "C2",
        "paths": 1,
    }
    alone = naming.parse_experiment_name("p2_perm_alone_C0_P4")
    assert alone is not None and alone["aggressor"] is None and alone["paths"] == 4
    cross = naming.parse_experiment_name("p0_cross_C0_P1")
    assert cross is not None and cross["victim"] == "cross" and cross["aggressor"] is None
    cong = naming.parse_experiment_name("cong_burst-0.1-0.01_C3_P1")
    assert cong is not None and cong["victim"] is None
    assert cong["aggressor"] == "burst-0.1-0.01"
    assert naming.experiment_name("p2", "ag_vs_pw", "C0", 1) == "p2_ag_vs_pw_C0_P1"


@pytest.mark.parametrize(
    "name",
    ["", "01_victim_alone", "corun_nics", "p2_perm_vs_x_C0_P1", "p9_perm_C0_P1", "p1_perm_C0_Px"],
)
def test_parse_experiment_name_rejects_unrelated_names(name: str) -> None:
    assert naming.parse_experiment_name(name) is None


def test_every_app_has_the_profile_placement(four_node: dict[str, dict[str, Any]]) -> None:
    cpus = "0,1,2,3,32,33,34,35"
    nics = [f"mlx5_{i}:1" for i in range(8)]
    for file_name, config in four_node.items():
        paths = file_name.split("_P")[1].split(".")[0]
        for exp_name, exp in config["experiments"].items():
            for app in exp["apps"].values():
                is_p0 = exp_name.startswith("p0_")
                assert app["ppn"] == (1 if is_p0 else 8), (file_name, exp_name)
                # a chained app runs on its head's cores: CRAB refuses a cpus key on it
                if app["start"].startswith("s"):
                    assert "cpus" not in app
                else:
                    assert app["cpus"] == ("0" if is_p0 else cpus)
                assert app["nics"] == (nics[:1] if is_p0 else nics)
                env = app["env"]
                assert env["UCX_MAX_RNDV_RAILS"] == "1"
                assert env["UCX_IB_NUM_PATHS"] == paths
                assert set(env) <= {"UCX_MAX_RNDV_RAILS", "UCX_IB_NUM_PATHS", "UCX_TLS"}


def test_no_node_names_in_any_file(tmp_path: Path) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(profile()))
    out = tmp_path / "out"
    assert make_configs.main([str(prof_path), "--out", str(out), "--net", "C0"]) == 0
    texts = [p.read_text() for p in out.glob("*.json")]
    assert texts
    for text in texts:
        for name in NODE_NAMES:
            assert name not in text
    assert {p.name for p in tmp_path.iterdir()} == {"profile.json", "out"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ppn": 4}, "ppn"),
        ({"cpus": "0,1,2"}, "cpus"),
        ({"nics": ["mlx5_0:1"]}, "ppn"),
        ({"nodes": ["a", "b"]}, "nodes"),
        ({"nodes": ["a", "b", "c", "d", "e"]}, "nodes"),
        ({"nodes": ["a"] * 2}, "nodes"),
    ],
)
def test_bad_profiles_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: dict[str, Any], message: str
) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps({**profile(), **change}))
    with pytest.raises(SystemExit):
        make_configs.main([str(prof_path), "--out", str(tmp_path / "out")])
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cpu_ranges_count_as_cores(tmp_path: Path) -> None:
    prof = profile(4, 4, cpus="0-1,32-33")
    configs = generate(tmp_path, prof, "--set", "md", "--net", "C0", "--paths", "1")
    assert app_list(configs["md_p0_C0_P1.json"], "p0_cross_C0_P1")[0]["cpus"] == "0"
    assert app_list(configs["md_p1_C0_P1.json"], "p1_a2a_C0_P1")[0]["cpus"] == "0-1,32-33"


def test_summary_lists_files_and_assumption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generate(tmp_path, profile(), "--set", "md", "--net", "C0", "--paths", "1")
    out = capsys.readouterr().out
    assert "md_p0_C0_P1.json" in out
    assert "md_p2_a2a_C0_P1.json" in out
    assert "12 experiments" in out
    assert "same NIC index" in out


def test_colleague_set_skips_c3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configs = generate(tmp_path, profile(), "--set", "colleague", "--net", "C0", "C3")
    assert all(name.startswith("col_") and "_C0_" in name for name in configs)
    assert len(configs) == 6
    assert "C0 and C2 only; C3 is left out" in capsys.readouterr().out
    (tmp_path / "again").mkdir()
    only_c3 = generate(tmp_path / "again", profile(), "--set", "colleague", "--net", "C3")
    assert only_c3 == {}


def grty_of(app: dict[str, Any]) -> int | None:
    words = app["args"].split()
    return int(words[words.index("-grty") + 1]) if "-grty" in words else None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (1, 100),
        (8, 100),
        (4096, 100),
        (3000, 100),
        (32768, 64),
        (262144, 8),
        (300000, 7),
        (2097152, 1),
        (16777216, 1),
        (536870912, 1),
    ],
)
def test_grty_rule(size: int, expected: int) -> None:
    assert make_configs.grty_for(size) == expected


def test_md_set_victims_get_grty_from_the_rule(
    four_node: dict[str, dict[str, Any]], tmp_path: Path
) -> None:
    smoke = generate(
        tmp_path, profile(4, 4), "--smoke", "--set", "md", "--net", "C0", "--paths", "1"
    )
    checked = 0
    for configs in (four_node, smoke):
        for name, config in configs.items():
            if "md_p" not in name:
                continue
            for exp in config["experiments"]:
                for app in app_list(config, exp):
                    if app["collect"]:
                        size = sizes_of([app])[0]
                        assert grty_of(app) == make_configs.grty_for(size), (name, exp, size)
                        checked += 1
                    else:
                        assert grty_of(app) is None, (name, exp)
    assert checked > 0
    p1 = app_list(four_node["md_p1_C0_P1.json"], "p1_a2a_C0_P1")
    assert [grty_of(a) for a in p1[:6]] == [100, 100, 100, 100, 100, 64]
    assert p1[0]["args"] == "-msgsize 1 -iter 1000 -grty 100"
    assert p1[5]["args"] == "-msgsize 32768 -iter 1000 -grty 64"
    assert p1[7]["args"] == "-msgsize 2097152 -iter 1000 -grty 1"


def test_colleague_set_has_no_grty(four_node: dict[str, dict[str, Any]]) -> None:
    chain = app_list(four_node["col_a2a_C2_P4.json"], "cong_burst-0.01-0.0001_C2_P4")[:-1]
    assert chain[0]["args"] == "-msgsize 8 -iter 1000"
    assert chain[4]["args"] == "-msgsize 32768 -iter 1000"
    assert all(grty_of(a) is None for a in chain)
