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


def _p2_experiments(configs: dict[str, dict[str, Any]], victim: str) -> set[str]:
    """The experiment names of one victim's phase-2 file on C0 with one path."""
    return set(configs[f"md_p2_{victim}_C0_P1.json"]["experiments"])


def test_aggressors_a2a_keeps_alone_and_the_steady_a2a(
    tmp_path: Path, four_node: dict[str, dict[str, Any]]
) -> None:
    from crab.core.config_checks import check_config

    configs = generate(tmp_path, profile(), "--aggressors", "a2a")
    assert set(configs) == set(four_node)
    for victim in ("perm", "a2a", "ag"):
        for net in ("C0", "C2"):
            for paths in (1, 4):
                config = configs[f"md_p2_{victim}_{net}_P{paths}.json"]
                assert set(config["experiments"]) == {
                    f"p2_{victim}_alone_{net}_P{paths}",
                    f"p2_{victim}_vs_a2a_{net}_P{paths}",
                }
    for name, config in configs.items():
        assert check_config(config, slurm_settings()) == [], name
        if "_p2_" not in name:  # phases 0 and 1 and the colleague set do not change
            assert config == four_node[name], name


def test_aggressors_burst_keeps_alone_and_every_burst(tmp_path: Path) -> None:
    configs = generate(tmp_path, profile(), "--aggressors", "burst")
    expected = {"p2_ag_alone_C0_P1"} | {
        f"p2_ag_vs_burst-{length}-{pause}_C0_P1" for length, pause in BURSTS
    }
    assert _p2_experiments(configs, "ag") == expected
    assert len(expected) == 10


def test_aggressors_incast_and_several_kinds(tmp_path: Path) -> None:
    configs = generate(tmp_path, profile(), "--aggressors", "incast", "a2a")
    assert _p2_experiments(configs, "perm") == {
        "p2_perm_alone_C0_P1",
        "p2_perm_vs_a2a_C0_P1",
        "p2_perm_vs_incast_C0_P1",
    }


def test_aggressors_default_is_every_kind(four_node: dict[str, dict[str, Any]]) -> None:
    expected = {"p2_perm_alone_C0_P1", "p2_perm_vs_a2a_C0_P1", "p2_perm_vs_incast_C0_P1"}
    expected |= {f"p2_perm_vs_burst-{length}-{pause}_C0_P1" for length, pause in BURSTS}
    assert _p2_experiments(four_node, "perm") == expected


def test_aggressors_the_three_node_layout_only_has_pw(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(profile(3, 8)))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as stop:
        make_configs.main([str(prof_path), "--out", str(out), "--aggressors", "a2a"])
    assert stop.value.code != 0
    assert "3-node layout has only the pw aggressor" in capsys.readouterr().err
    assert not out.exists()
    (tmp_path / "ok").mkdir()
    configs = generate(tmp_path / "ok", profile(3, 8), "--set", "md", "--aggressors", "pw", "a2a")
    assert _p2_experiments(configs, "perm") == {"p2_perm_alone_C0_P1", "p2_perm_vs_pw_C0_P1"}


def test_aggressors_pw_is_refused_on_four_nodes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(profile()))
    with pytest.raises(SystemExit):
        make_configs.main([str(prof_path), "--out", str(tmp_path / "out"), "--aggressors", "pw"])
    assert "pw is only for the 3-node layout" in capsys.readouterr().err


def test_aggressors_do_not_matter_for_the_colleague_set_alone(tmp_path: Path) -> None:
    configs = generate(tmp_path, profile(3, 8), "--set", "colleague", "--aggressors", "a2a")
    assert configs


def test_summary_names_the_selected_aggressors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generate(tmp_path, profile(), "--set", "md", "--net", "C0", "--paths", "1")
    assert "Phase-2 aggressors: a2a, burst, incast" in capsys.readouterr().out
    (tmp_path / "again").mkdir()
    generate(
        tmp_path / "again", profile(), "--set", "md", "--aggressors", "incast", "a2a", "--net", "C0"
    )
    assert "Phase-2 aggressors: a2a, incast" in capsys.readouterr().out


def test_tag_is_appended_to_stems_and_names_but_not_experiments(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config

    plain = generate(tmp_path, profile(), "--paths", "1")
    (tmp_path / "tagged").mkdir()
    tagged = generate(tmp_path / "tagged", profile(), "--paths", "1", "--tag", "ppn8")
    assert set(tagged) == {f"{name[: -len('.json')]}_ppn8.json" for name in plain}
    for name, config in tagged.items():
        stem = name[: -len(".json")]
        old = plain[f"{stem[: -len('_ppn8')]}.json"]
        opts = config["global_options"]
        assert opts["name"] == stem and opts["name"].endswith("_ppn8")
        assert opts["sbatch_directives"][1] == f"--job-name=nplb_{stem}"
        assert config["experiments"] == old["experiments"], name
        assert list(config["experiments"]) == list(old["experiments"]), name
        rest = {k: v for k, v in opts.items() if k not in ("name", "sbatch_directives")}
        old_opts = old["global_options"]
        assert rest == {k: v for k, v in old_opts.items() if k not in ("name", "sbatch_directives")}
        assert check_config(config, slurm_settings()) == [], name


def test_tag_with_smoke_goes_after_the_whole_stem(tmp_path: Path) -> None:
    configs = generate(
        tmp_path, profile(4, 4), "--smoke", "--net", "C0", "--paths", "1", "--tag", "ppn4"
    )
    assert "smoke_md_p1_C0_P1_ppn4.json" in configs
    assert "smoke_col_a2a_C0_P1_ppn4.json" in configs
    assert all(n.startswith("smoke_") and n.endswith("_ppn4.json") for n in configs)
    assert configs["smoke_md_p1_C0_P1_ppn4.json"]["global_options"]["name"] == (
        "smoke_md_p1_C0_P1_ppn4"
    )


def test_no_tag_is_the_default_output(tmp_path: Path, four_node: dict[str, dict[str, Any]]) -> None:
    assert generate(tmp_path, profile(4, 8)) == four_node


@pytest.mark.parametrize("tag", ["a_b", "a b", "", "ppn8!"])
def test_bad_tags_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], tag: str) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(profile()))
    with pytest.raises(SystemExit) as stop:
        make_configs.main([str(prof_path), "--out", str(tmp_path / "out"), "--tag", tag])
    assert stop.value.code == 2
    assert "--tag" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_summary_mentions_the_tag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    generate(tmp_path, profile(), "--set", "md", "--net", "C0", "--paths", "1", "--tag", "ppn8")
    out = capsys.readouterr().out
    assert "md_p0_C0_P1_ppn8.json" in out
    assert "Tag: ppn8" in out


# --- ping-pong set (--set pingpong) ---------------------------------------------------------


def _pci(card: int, function: int) -> str:
    return f"0000:{0x19 + 0x10 * card:02x}:00.{function}"


def per_card_profile(nodes: int = 4) -> dict[str, Any]:
    """profile8: the first port of each of 8 cards."""
    per_nic = {
        f"mlx5_{2 * k}:1": {"numa": k // 4, "core": 4 * k, "pci": _pci(k, 0), "speed_mbps": 400000}
        for k in range(8)
    }
    return profile(
        nodes,
        8,
        nics=list(per_nic),
        cpus=",".join(str(v["core"]) for v in per_nic.values()),
        per_nic=per_nic,
    )


def all_ports_profile(missing: tuple[str, ...] = ()) -> dict[str, Any]:
    """profile16: both ports of each of 8 cards; `missing` names devices left out."""
    per_nic = {
        f"mlx5_{2 * k + f}:1": {"numa": k // 4, "core": 4 * k + f, "pci": _pci(k, f)}
        for k in range(8)
        for f in (0, 1)
    }
    for name in missing:
        del per_nic[name]
    return profile(4, 16, nics=list(per_nic), per_nic=per_nic)


def pingpong(
    tmp_path: Path,
    nodes: int = 4,
    *args: str,
    all_ports: dict[str, Any] | None = None,
    prof: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run `--set pingpong` on the fake profiles into tmp_path/local/out."""
    (tmp_path / "ports.json").write_text(json.dumps(all_ports or all_ports_profile()))
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(prof or per_card_profile(nodes)))
    out = tmp_path / "local" / "out"
    argv = [str(prof_path), "--out", str(out), "--set", "pingpong", "--all-ports"]
    argv += [str(tmp_path / "ports.json"), "--net", "C2", "--paths", "1", *args]
    assert make_configs.main(argv) == 0
    return {p.name: json.loads(p.read_text()) for p in sorted(out.glob("*.json"))}


@pytest.fixture(scope="module")
def pp_four(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return pingpong(tmp_path_factory.mktemp("pp4"))


def test_pingpong_file_set_per_node_count(tmp_path: Path, pp_four: dict[str, Any]) -> None:
    assert set(pp_four) == {
        "pp_n1n2_C2_P1.json",
        "pp_n1n3_C2_P1.json",
        "pp_n1n4_C2_P1.json",
        "pp_n1n3_pw8_C2_P1.json",
    }
    for label in ("n1n2", "n1n3", "n1n4"):
        config = pp_four[f"pp_{label}_C2_P1.json"]
        assert list(config["experiments"]) == [
            f"pp_{label}-{v}_C2_P1" for v in ("md", "default", "dual")
        ]
    assert list(pp_four["pp_n1n3_pw8_C2_P1.json"]["experiments"]) == [
        "pp_n1n3-pw8_C2_P1",
        "pp_n1n3-pw8d_C2_P1",
    ]
    (tmp_path / "two").mkdir()
    two = pingpong(tmp_path / "two", 2)
    assert set(two) == {"pp_n1n2_C2_P1.json", "pp_n1n2_pw8_C2_P1.json"}
    assert list(two["pp_n1n2_pw8_C2_P1.json"]["experiments"]) == [
        "pp_n1n2-pw8_C2_P1",
        "pp_n1n2-pw8d_C2_P1",
    ]
    (tmp_path / "three").mkdir()
    three = pingpong(tmp_path / "three", 3)
    assert set(three) == {"pp_n1n2_C2_P1.json", "pp_n1n3_C2_P1.json", "pp_n1n3_pw8_C2_P1.json"}


def test_pingpong_configs_pass_the_checks(pp_four: dict[str, Any]) -> None:
    from crab.core.config_checks import check_config

    for name, config in pp_four.items():
        assert config["schema_version"] == 2, name
        assert check_config(config, slurm_settings()) == [], name


def test_pingpong_global_options(pp_four: dict[str, Any], tmp_path: Path) -> None:
    pairs = {"n1n2": "node-b", "n1n3": "node-c", "n1n4": "node-d"}
    for label, other in pairs.items():
        opts = pp_four[f"pp_{label}_C2_P1.json"]["global_options"]
        assert opts["numnodes"] == "2" and opts["ppn"] == "1"
        assert opts["minruns"] == opts["maxruns"] == "5"
        assert opts["walltime"] == "02:00:00"
        assert opts["sbatch_directives"] == [
            "--exclusive",
            f"--job-name=nplb_pp_{label}_C2_P1",
            f"--nodelist=node-a,{other}",
        ]
        assert "allocation" not in opts
    pw8 = pp_four["pp_n1n3_pw8_C2_P1.json"]["global_options"]
    assert pw8["numnodes"] == "2" and pw8["ppn"] == "8" and "allocation" not in pw8
    assert pw8["sbatch_directives"][2] == "--nodelist=node-a,node-c"
    (tmp_path / "over").mkdir()
    over = pingpong(
        tmp_path / "over", 4, "--minruns", "3", "--maxruns", "7", "--walltime", "01:00:00"
    )
    opts = over["pp_n1n2_C2_P1.json"]["global_options"]
    assert (opts["minruns"], opts["maxruns"], opts["walltime"]) == ("3", "7", "01:00:00")


def test_pingpong_variants(pp_four: dict[str, Any]) -> None:
    experiments = pp_four["pp_n1n3_C2_P1.json"]["experiments"]
    for variant in ("md", "default", "dual"):
        apps = app_list(pp_four["pp_n1n3_C2_P1.json"], f"pp_n1n3-{variant}_C2_P1")
        assert {a["path"] for a in apps} == {"blink/pingpong_b.py"}
        assert {a["ppn"] for a in apps} == {1}
        assert [a["start"] for a in apps] == ["0"] + [f"s{k}" for k in range(10)]
        head = apps[0]
        assert head["cpus"] == "0" and all("cpus" not in a for a in apps[1:])
        for app in apps:
            if variant == "dual":
                assert "nics" not in app
            else:
                assert app["nics"] == ["mlx5_0:1"]
    md = app_list(pp_four["pp_n1n3_C2_P1.json"], "pp_n1n3-md_C2_P1")
    assert all(a["env"] == {"UCX_MAX_RNDV_RAILS": "1", "UCX_IB_NUM_PATHS": "1"} for a in md)
    default = app_list(pp_four["pp_n1n3_C2_P1.json"], "pp_n1n3-default_C2_P1")
    assert all(not any(k.startswith("UCX_") for k in a.get("env", {})) for a in default)
    dual = app_list(pp_four["pp_n1n3_C2_P1.json"], "pp_n1n3-dual_C2_P1")
    assert all(
        a["env"]
        == {
            "UCX_NET_DEVICES": "mlx5_0:1,mlx5_1:1",
            "UCX_MAX_RNDV_RAILS": "2",
            "UCX_IB_NUM_PATHS": "1",
        }
        for a in dual
    )
    assert len(experiments) == 3


def test_pingpong_program_and_sweep(pp_four: dict[str, Any], tmp_path: Path) -> None:
    pw8 = app_list(pp_four["pp_n1n3_pw8_C2_P1.json"], "pp_n1n3-pw8_C2_P1")
    assert {a["path"] for a in pw8} == {"blink/pairwise_nb.py"}
    assert all(a["args"].startswith("-mode rot -offset 8 -msgsize ") for a in pw8)
    assert {a["ppn"] for a in pw8} == {8}
    assert pw8[0]["cpus"] == "0,4,8,12,16,20,24,28"
    assert pw8[0]["nics"] == [f"mlx5_{2 * k}:1" for k in range(8)]
    assert all(a["env"] == {"UCX_MAX_RNDV_RAILS": "1", "UCX_IB_NUM_PATHS": "1"} for a in pw8)
    for config, exp in (
        (pp_four["pp_n1n3_pw8_C2_P1.json"], "pp_n1n3-pw8_C2_P1"),
        (pp_four["pp_n1n3_pw8_C2_P1.json"], "pp_n1n3-pw8d_C2_P1"),
        (pp_four["pp_n1n2_C2_P1.json"], "pp_n1n2-md_C2_P1"),
        (pp_four["pp_n1n2_C2_P1.json"], "pp_n1n2-dual_C2_P1"),
    ):
        apps = app_list(config, exp)
        assert sizes_of(apps) == MD_SIZES
        assert [iter_of(a) for a in apps] == [1000] * 9 + [100, 25]
        assert [grty_of(a) for a in apps] == [make_configs.grty_for(s) for s in MD_SIZES]
    (tmp_path / "iter").mkdir()
    flags = ("--iter-small", "300", "--iter-128m", "60", "--iter-512m", "7")
    iters = pingpong(tmp_path / "iter", 4, *flags)
    apps = app_list(iters["pp_n1n2_C2_P1.json"], "pp_n1n2-md_C2_P1")
    assert [iter_of(a) for a in apps] == [300] * 9 + [60, 7]


def test_pingpong_without_a_sibling_omits_dual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configs = pingpong(tmp_path, 4, all_ports=all_ports_profile(missing=("mlx5_1:1",)))
    for label in ("n1n2", "n1n3", "n1n4"):
        assert list(configs[f"pp_{label}_C2_P1.json"]["experiments"]) == [
            f"pp_{label}-md_C2_P1",
            f"pp_{label}-default_C2_P1",
        ]
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "dual" in ln]
    assert lines and all("mlx5_0:1" in ln for ln in lines)


def test_pingpong_summary_lists_the_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pingpong(tmp_path)
    out = capsys.readouterr().out
    assert "pp_n1n2_C2_P1.json: 3 experiments" in out
    assert "pp_n1n3_pw8_C2_P1.json: 2 experiments" in out
    assert "dual" not in out.replace("pp_n1n2-dual", "")  # no omission note when all are written


def test_pingpong_needs_all_ports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(per_card_profile()))
    argv = [str(prof_path), "--out", str(tmp_path / "local" / "o"), "--set", "pingpong"]
    with pytest.raises(SystemExit) as stop:
        make_configs.main([*argv, "--net", "C2", "--paths", "1"])
    assert stop.value.code == 2
    assert "--all-ports" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra", [[], ["--net", "C0", "C2", "--paths", "1"], ["--net", "C2", "--paths", "1", "4"]]
)
def test_pingpong_takes_one_net_and_one_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    (tmp_path / "ports.json").write_text(json.dumps(all_ports_profile()))
    (tmp_path / "profile.json").write_text(json.dumps(per_card_profile()))
    argv = [str(tmp_path / "profile.json"), "--out", str(tmp_path / "local" / "o")]
    argv += ["--set", "pingpong", "--all-ports", str(tmp_path / "ports.json"), *extra]
    with pytest.raises(SystemExit) as stop:
        make_configs.main(argv)
    assert stop.value.code == 2
    assert "exactly one" in capsys.readouterr().err
    assert not (tmp_path / "local").exists()


def test_pingpong_refuses_an_out_outside_local(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ports.json").write_text(json.dumps(all_ports_profile()))
    (tmp_path / "profile.json").write_text(json.dumps(per_card_profile()))
    argv = [str(tmp_path / "profile.json"), "--out", str(tmp_path / "configs")]
    argv += ["--set", "pingpong", "--all-ports", str(tmp_path / "ports.json")]
    argv += ["--net", "C2", "--paths", "1"]
    with pytest.raises(SystemExit) as stop:
        make_configs.main(argv)
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert "node names" in err and "local/" in err
    assert not (tmp_path / "configs").exists()


def test_pingpong_tag_keeps_the_nodelist(tmp_path: Path) -> None:
    from crab.core.config_checks import check_config

    configs = pingpong(tmp_path, 4, "--tag", "ppn8")
    assert set(configs) == {
        "pp_n1n2_C2_P1_ppn8.json",
        "pp_n1n3_C2_P1_ppn8.json",
        "pp_n1n4_C2_P1_ppn8.json",
        "pp_n1n3_pw8_C2_P1_ppn8.json",
    }
    for name, config in configs.items():
        stem = name[: -len(".json")]
        opts = config["global_options"]
        assert opts["name"] == stem
        assert opts["sbatch_directives"][1] == f"--job-name=nplb_{stem}"
        assert opts["sbatch_directives"][2].startswith("--nodelist=node-a,node-")
        assert check_config(config, slurm_settings()) == [], name


def test_pingpong_names_round_trip(pp_four: dict[str, Any]) -> None:
    seen = 0
    for config in pp_four.values():
        for exp in config["experiments"]:
            parsed = naming.parse_experiment_name(exp)
            assert parsed is not None, exp
            assert (
                naming.experiment_name(
                    parsed["phase"], parsed["test"], parsed["net"], parsed["paths"]
                )
                == exp
            )
            seen += 1
    assert seen == 11


def test_parse_pingpong_name_fields() -> None:
    assert naming.parse_experiment_name("pp_n1n3-pw8_C2_P1") == {
        "phase": "pp",
        "test": "n1n3-pw8",
        "victim": "n1n3-pw8",
        "aggressor": None,
        "net": "C2",
        "paths": 1,
    }
    for bad in ("pp_n1n3-foo_C2_P1", "pp_x-md_C2_P1", "pp_n1n3_C2_P1"):
        assert naming.parse_experiment_name(bad) is None


def test_pingpong_set_is_not_part_of_both(four_node: dict[str, dict[str, Any]]) -> None:
    assert not any(name.startswith("pp_") for name in four_node)


# --- ping-pong set: N ranks per node, warm-up, -maxsamples ----------------------------------


def eight_card_profile() -> dict[str, Any]:
    """profile8 with the first port of 8 cards and cores 0-3, 40-43."""
    prof = per_card_profile()
    prof["cpus"] = "0,1,2,3,40,41,42,43"
    return prof


def every_app(configs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for c in configs.values() for e in c["experiments"] for a in app_list(c, e)]


@pytest.fixture(scope="module")
def pp_ranks4(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return pingpong(tmp_path_factory.mktemp("pp-r4"), 4, "--ranks", "4", prof=eight_card_profile())


def test_pingpong_ranks_names_the_file_and_the_experiments(pp_ranks4: dict[str, Any]) -> None:
    assert set(pp_ranks4) == {
        "pp_n1n2_C2_P1.json",
        "pp_n1n3_C2_P1.json",
        "pp_n1n4_C2_P1.json",
        "pp_n1n3_pw4_C2_P1.json",
    }
    config = pp_ranks4["pp_n1n3_pw4_C2_P1.json"]
    assert list(config["experiments"]) == ["pp_n1n3-pw4_C2_P1", "pp_n1n3-pw4d_C2_P1"]
    assert config["global_options"]["ppn"] == "4"


def test_pingpong_ranks_take_every_other_card_and_core(pp_ranks4: dict[str, Any]) -> None:
    config = pp_ranks4["pp_n1n3_pw4_C2_P1.json"]
    for exp in config["experiments"]:
        apps = app_list(config, exp)
        assert apps and {a["ppn"] for a in apps} == {4}
        assert all(a["nics"] == ["mlx5_0:1", "mlx5_4:1", "mlx5_8:1", "mlx5_12:1"] for a in apps)
        assert apps[0]["cpus"] == "0,2,40,42"
        assert all(a["args"].startswith("-mode rot -offset 4 -msgsize ") for a in apps)


def test_pingpong_ranks_ucx_env_on_and_off(pp_ranks4: dict[str, Any]) -> None:
    config = pp_ranks4["pp_n1n3_pw4_C2_P1.json"]
    on = app_list(config, "pp_n1n3-pw4_C2_P1")
    assert all(a["env"] == {"UCX_MAX_RNDV_RAILS": "1", "UCX_IB_NUM_PATHS": "1"} for a in on)
    off = app_list(config, "pp_n1n3-pw4d_C2_P1")
    assert all(not any(k.startswith("UCX_") for k in a.get("env", {})) for a in off)
    assert all(len(a["nics"]) == 4 for a in on + off)


def test_pingpong_pairs_are_the_same_with_ranks(
    pp_ranks4: dict[str, Any], pp_four: dict[str, Any]
) -> None:
    for label in ("n1n2", "n1n3", "n1n4"):
        name = f"pp_{label}_C2_P1.json"
        assert pp_ranks4[name]["experiments"] == pp_four[name]["experiments"]


def test_pingpong_without_ranks_has_both_node_total_experiments(pp_four: dict[str, Any]) -> None:
    config = pp_four["pp_n1n3_pw8_C2_P1.json"]
    plain = app_list(config, "pp_n1n3-pw8_C2_P1")
    off = app_list(config, "pp_n1n3-pw8d_C2_P1")
    assert [a["args"] for a in plain] == [a["args"] for a in off]
    assert all(a["env"] == {"UCX_MAX_RNDV_RAILS": "1", "UCX_IB_NUM_PATHS": "1"} for a in plain)
    assert all("env" not in a for a in off)
    assert all(a["nics"] == plain[0]["nics"] and a["ppn"] == 8 for a in off)


@pytest.mark.parametrize("ranks", ["3", "0", "-2", "9"])
def test_pingpong_ranks_must_divide_the_ppn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], ranks: str
) -> None:
    with pytest.raises(SystemExit) as stop:
        pingpong(tmp_path, 4, "--ranks", ranks)
    assert stop.value.code == 2
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert "--ranks" in err and "ppn" in err
    assert not (tmp_path / "local" / "out").exists()


def test_ranks_needs_the_pingpong_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "profile.json").write_text(json.dumps(profile()))
    argv = [str(tmp_path / "profile.json"), "--out", str(tmp_path / "out"), "--ranks", "4"]
    with pytest.raises(SystemExit) as stop:
        make_configs.main(argv)
    assert stop.value.code == 2
    assert "--ranks" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_subset_profile_keeps_every_nth_nic_and_core() -> None:
    layout = make_configs.PAIR_LAYOUT
    nics = tuple(f"n{i}" for i in range(8))
    cores = (0, 1, 2, 3, 40, 41, 42, 43)
    prof = make_configs.Profile(8, nics, "0,1,2,3,40,41,42,43", cores, 1800.0, layout, ("a", "b"))
    sub = make_configs.subset_profile(prof, 2)
    assert (sub.ppn, sub.nics, sub.cpus, sub.cores) == (2, ("n0", "n4"), "0,40", (0, 40))
    assert sub.nodes == ("a", "b") and sub.mem_gib == 1800.0
    assert make_configs.subset_profile(prof, 8) == prof


def test_warmup_reaches_every_pingpong_app(tmp_path: Path) -> None:
    apps = every_app(pingpong(tmp_path, 4, "--warmup", "200"))
    assert apps and all(" -warmup 200" in a["args"] for a in apps)


def test_warmup_zero_is_written_and_default_is_absent(
    tmp_path: Path, pp_four: dict[str, Any], four_node: dict[str, Any]
) -> None:
    zero = every_app(pingpong(tmp_path, 4, "--warmup", "0"))
    assert zero and all(" -warmup 0" in a["args"] for a in zero)
    for files in (pp_four, four_node):
        assert all("-warmup" not in a["args"] for a in every_app(files))


def test_warmup_refuses_a_negative_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        pingpong(tmp_path, 4, "--warmup", "-1")
    assert stop.value.code == 2
    assert "--warmup" in capsys.readouterr().err


def test_maxsamples_follows_iterations_over_1000(tmp_path: Path) -> None:
    flags = ("--iter-small", "2000", "--iter-128m", "1000", "--iter-512m", "7")
    pp = every_app(pingpong(tmp_path, 4, *flags))
    assert {iter_of(a) for a in pp} == {2000, 1000, 7}
    for app in pp:
        words = app["args"].split()
        if iter_of(app) > 1000:
            assert words[words.index("-maxsamples") + 1] == str(iter_of(app))
        else:
            assert "-maxsamples" not in words
    (tmp_path / "md").mkdir()
    md = [
        a
        for a in every_app(
            generate(tmp_path / "md", profile(), "--set", "md", "--iter-small", "1500")
        )
        if "-iter" in a["args"]
    ]
    assert {iter_of(a) for a in md} == {1500, 100, 25}
    assert all(("-maxsamples" in a["args"]) == (iter_of(a) > 1000) for a in md)


def test_maxsamples_is_absent_with_default_iterations(
    four_node: dict[str, Any], pp_four: dict[str, Any]
) -> None:
    for files in (four_node, pp_four):
        assert all("-maxsamples" not in a["args"] for a in every_app(files))


def test_parse_node_total_names_with_any_rank_count() -> None:
    for name in ("pp_n1n3-pw4_C2_P1", "pp_n1n3-pw4d_C2_P1", "pp_n1n3-pw16_C2_P1"):
        parsed = naming.parse_experiment_name(name)
        assert parsed is not None and parsed["phase"] == "pp", name
    assert naming.parse_experiment_name("pp_n1n3-pwx_C2_P1") is None
