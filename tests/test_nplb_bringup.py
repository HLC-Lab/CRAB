"""examples/nplb/env.sh and bringup.sh, run with bash in a fake checkout under tmp_path.

The checkout holds copies of the real scripts (bringup.sh, env.sh, make_configs.py and its
modules), a fake nic_check.sh and analyze.py, and a fake `.venv` whose python answers the Blink
receipt query and whose crab prints a submit JSON. salloc, squeue and sacct are fakes on PATH.
Nothing here talks to Slurm or a cluster."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
COPIED = [
    "bringup.sh",
    "env.sh",
    "make_configs.py",
    "naming.py",
    "md_set.py",
    "colleague_set.py",
    "pingpong_set.py",
]
MPIRUN_FLAGS = "--allow-run-as-root --mca pml ucx --mca btl ^openib"
NICS = [f"mlx5_{i}:1" for i in range(8)]

FAKE_SALLOC = r"""#!/usr/bin/env bash
echo "salloc $*" >>"$FAKE_LOG"
while [[ $# -gt 0 ]]; do
    case $1 in
        -p | -t | -A) shift 2 ;;
        -*) shift ;;
        *) break ;;
    esac
done
exec "$@"
"""
FAKE_SQUEUE = '#!/usr/bin/env bash\necho "squeue $*" >>"$FAKE_LOG"\necho "JOBID PARTITION fake"\n'
FAKE_SACCT = '#!/usr/bin/env bash\necho "sacct $*" >>"$FAKE_LOG"\necho "${FAKE_STATE:-COMPLETED}"\n'
FAKE_MPIRUN = "#!/usr/bin/env bash\nexit 0\n"
FAKE_NIC_CHECK = r"""#!/usr/bin/env bash
echo "nic_check LAUNCHER=$LAUNCHER MPIRUN_FLAGS=$MPIRUN_FLAGS args=$*" >>"$FAKE_LOG"
echo "fake nic check output"
"""
FAKE_TOPOLOGY = r"""#!/usr/bin/env bash
echo "topology args=$* PARTITION=${PARTITION-unset} WORK=${WORK-unset}" >>"$FAKE_LOG"
echo "fake topology summary"
exit "${FAKE_TOPO_RC:-0}"
"""
FAKE_ANALYZE = r"""import sys
from pathlib import Path

args = sys.argv[1:]
out = Path(args[args.index("--out") + 1])
dirs = [a for a in args[: args.index("--out")]]
with open(__import__("os").environ["FAKE_LOG"], "a") as log:
    log.write("analyze " + " ".join(dirs) + "\n")
out.mkdir(parents=True, exist_ok=True)
(out / "summary.md").write_text("".join(f"summary line {i}\n" for i in range(1, 71)))
"""
FAKE_CRAB = r"""#!/usr/bin/env bash
echo "crab $*" >>"$FAKE_LOG"
echo "Loading preset (fake log line)" >&2
if [[ ${FAKE_CRAB_FAIL:-} == 1 ]]; then
    echo "Orchestrator fatal error: fake" >&2
    exit 1
fi
[[ $1 == run && $2 == -p && $4 == --json ]] || exit 3
n=$(($(cat "$FAKE_COUNT" 2>/dev/null || echo 1000) + 1))
echo "$n" >"$FAKE_COUNT"
name=$(basename "$5" .json)
printf '{\n  "job_id": "%s",\n  "data_dir": "%s/sys/%s_2026-10-14_10-00-00-000000",\n' \
    "$n" "$FAKE_DATA" "$name"
printf '  "system": "sys"\n}\n'
"""


def fake_python() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "if [[ ${1:-} == -c && ${2:-} == *crab.setup.memory* ]]; then\n"
        "    [[ -n ${FAKE_NO_RECEIPT:-} ]] && exit 1\n"
        '    echo "$FAKE_BLINK"\n'
        "    exit 0\n"
        "fi\n"
        f'exec {sys.executable} "$@"\n'
    )


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def site(tmp_path: Path) -> dict:
    chk = tmp_path / "chk"
    nplb = chk / "examples" / "nplb"
    nplb.mkdir(parents=True)
    for name in COPIED:
        shutil.copy(NPLB / name, nplb / name)
    write_exec(nplb / "nic_check.sh", FAKE_NIC_CHECK)
    write_exec(nplb / "topology.sh", FAKE_TOPOLOGY)
    (nplb / "analyze.py").write_text(FAKE_ANALYZE)
    write_exec(chk / ".venv" / "bin" / "python", fake_python())
    write_exec(chk / ".venv" / "bin" / "crab", FAKE_CRAB)
    bindir = tmp_path / "bin"
    for name, text in (("salloc", FAKE_SALLOC), ("squeue", FAKE_SQUEUE), ("sacct", FAKE_SACCT)):
        write_exec(bindir / name, text)
    blink = tmp_path / "blink"
    blink.mkdir()
    mpi_root = tmp_path / "mpi"
    for version in ("4.0.2", "4.1.5a1"):
        write_exec(mpi_root / f"openmpi-{version}" / "bin" / "mpirun", FAKE_MPIRUN)
    nodes = [f"n{i}" for i in range(4)]
    for ppn in (8, 16):
        profile = {
            "nodes": nodes,
            "ppn": ppn,
            "nics": [f"mlx5_{i}:1" for i in range(ppn)],
            "cpus": f"0-{ppn - 1}",
        }
        if ppn == 16:  # the all-ports profile of the probe's --merge: two ports per card
            profile["per_nic"] = {
                f"mlx5_{i}:1": {"pci": f"0000:{i // 2:02x}:00.{i % 2}"} for i in range(ppn)
            }
        (chk / f"profile{ppn}.json").write_text(json.dumps(profile))
    return {
        "root": tmp_path,
        "chk": chk,
        "bringup": nplb / "bringup.sh",
        "env_sh": nplb / "env.sh",
        "bin": bindir,
        "blink": blink,
        "mpi_root": mpi_root,
        "log": tmp_path / "log",
        "data": tmp_path / "data",
        "count": tmp_path / "count",
        "elsewhere": tmp_path / "elsewhere",
    }


def env_for(site: dict, **extra: str) -> dict:
    site["elsewhere"].mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{site['bin']}:{os.environ['PATH']}",
        "NPLB_MPI_ROOT": str(site["mpi_root"]),
        "FAKE_LOG": str(site["log"]),
        "FAKE_COUNT": str(site["count"]),
        "FAKE_DATA": str(site["data"]),
        "FAKE_BLINK": str(site["blink"]),
        "DATA_ROOT": str(site["data"]),
    }
    for var in ("PRESET", "PARTITION", "NET", "PATHS", "RUNS", "WORK", "PROFILE_DIR", "BLINK"):
        env.pop(var, None)
    env.update(extra)
    return env


def bringup(site: dict, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(site["bringup"]), *args],
        env=env_for(site, **env),
        cwd=site["elsewhere"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def log_lines(site: dict, prefix: str) -> list[str]:
    if not site["log"].exists():
        return []
    return [ln for ln in site["log"].read_text().splitlines() if ln.startswith(prefix)]


def run_log(site: dict) -> list[list[str]]:
    path = site["chk"] / "local" / "nplb-work" / "run_log.tsv"
    return [ln.split("\t") for ln in path.read_text().splitlines()]


# ---- env.sh ----


def source_env(site: dict, script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{site["env_sh"]}"; . "{site["env_sh"]}"; {script}'],
        env=env_for(site, **env),
        cwd=site["elsewhere"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_env_sh_sets_up_the_session_idempotently(site):
    (site["chk"] / "local").mkdir()
    (site["chk"] / "local" / "nplb_env.sh").write_text("export SITE_MARKER=loaded\n")
    result = source_env(
        site, 'echo "PATH=$PATH"; echo "PWD=$PWD"; echo "M=$SITE_MARKER"; echo "B=$BLINK"'
    )
    assert result.returncode == 0, result.stderr
    mpi_bin = str(site["mpi_root"] / "openmpi-4.1.5a1" / "bin")
    lines = result.stdout.splitlines()
    path = next(ln for ln in lines if ln.startswith("PATH=")).removeprefix("PATH=").split(":")
    assert path[0] == mpi_bin
    assert path.count(mpi_bin) == 1
    assert not any("openmpi-4.0.2" in p for p in path)
    assert f"PWD={site['chk']}" in lines
    assert "M=loaded" in lines
    assert f"B={site['blink']}" in lines
    assert lines.count(f"mpirun: {mpi_bin}/mpirun") == 2  # printed once per sourcing
    assert lines.count(f"blink: {site['blink']}") == 2


def test_env_sh_warns_and_returns_zero_without_mpi_or_receipt(site, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "sort", "tail", "tr", "grep", "paste", "cat"):
        found = shutil.which(name)
        assert found, name
        (tools / name).symlink_to(found)
    empty = tmp_path / "no-mpi"
    empty.mkdir()
    result = subprocess.run(
        [str(tools / "bash"), "-c", f'. "{site["env_sh"]}"; echo "rc=$?"'],
        env={**env_for(site, NPLB_MPI_ROOT=str(empty), FAKE_NO_RECEIPT="1"), "PATH": str(tools)},
        cwd=site["elsewhere"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "rc=0" in result.stdout
    assert "warning: no mpirun" in result.stdout
    assert "warning: no Blink directory" in result.stdout
    assert "mpirun:" not in result.stdout


# ---- nic ----


def test_nic_runs_nic_check_in_a_two_node_allocation(site):
    result = bringup(site, "nic", "8")
    assert result.returncode == 0, result.stderr
    assert log_lines(site, "salloc") == [
        "salloc -N2 --exclusive -p h100_4nodes -t 00:20:00 env"
        f" LAUNCHER=mpirun MPIRUN_FLAGS={MPIRUN_FLAGS} {site['chk']}/examples/nplb/nic_check.sh"
        f" {site['chk']}/profile8.json {site['blink']}"
    ]
    assert log_lines(site, "nic_check") == [
        f"nic_check LAUNCHER=mpirun MPIRUN_FLAGS={MPIRUN_FLAGS} args={site['chk']}/profile8.json "
        f"{site['blink']}"
    ]
    outputs = sorted((site["chk"] / "local" / "nplb-work").glob("nic_check_ppn8_*.txt"))
    assert len(outputs) == 1
    assert "fake nic check output" in outputs[0].read_text()
    assert f"NIC check output: {outputs[0]}" in result.stdout
    assert "+ salloc -N2" in result.stdout  # the command is echoed before it runs


def test_nic_honours_partition_and_work_overrides(site, tmp_path):
    work = tmp_path / "w"
    result = bringup(site, "nic", "16", PARTITION="other", WORK=str(work))
    assert result.returncode == 0, result.stderr
    assert "-p other " in log_lines(site, "salloc")[0]
    assert list(work.glob("nic_check_ppn16_*.txt"))


# ---- smoke and real ----


def test_smoke_generates_configs_and_submits_p0_p1_and_p2_a2a(site):
    old = site["chk"] / "local" / "nplb-work" / "smoke_ppn8"
    old.mkdir(parents=True)
    (old / "stale.json").write_text("{}")
    result = bringup(site, "smoke", "8")
    assert result.returncode == 0, result.stderr
    out = site["chk"] / "local" / "nplb-work" / "smoke_ppn8"
    assert not (old / "stale.json").exists()
    assert (out / "smoke_md_p2_perm_C2_P1_ppn8.json").exists()  # generated, not submitted
    runs = log_lines(site, "crab ")
    names = [
        "smoke_md_p0_C2_P1_ppn8.json",
        "smoke_md_p1_C2_P1_ppn8.json",
        "smoke_md_p2_a2a_C2_P1_ppn8.json",
    ]
    assert runs == [f"crab run -p nplb_h100 --json {out / n}" for n in names]
    rows = run_log(site)
    assert len(rows) == 3
    for row, name, job in zip(rows, names, ("1001", "1002", "1003"), strict=True):
        assert row[1:6] == ["C2", "1", "8", str(out / name), job]
        assert row[6] == f"{site['data']}/sys/{Path(name).stem}_2026-10-14_10-00-00-000000"
    assert "1001" in result.stdout and "1003" in result.stdout
    assert "bringup.sh status" in result.stdout
    assert "--smoke" in result.stdout and "--aggressors a2a" in result.stdout
    cfg = json.loads((out / names[0]).read_text())
    assert cfg["global_options"]["name"] == "smoke_md_p0_C2_P1_ppn8"


def test_real_submits_only_p2_a2a_with_the_runs_setting(site):
    result = bringup(site, "real", "8", RUNS="3")
    assert result.returncode == 0, result.stderr
    out = site["chk"] / "local" / "nplb-work" / "real_ppn8"
    assert log_lines(site, "crab ") == [
        f"crab run -p nplb_h100 --json {out / 'md_p2_a2a_C2_P1_ppn8.json'}"
    ]
    opts = json.loads((out / "md_p2_a2a_C2_P1_ppn8.json").read_text())["global_options"]
    assert (opts["minruns"], opts["maxruns"]) == ("3", "3")
    assert not list(out.glob("smoke_*"))
    assert len(run_log(site)) == 1
    assert "--minruns 3 --maxruns 3" in result.stdout


def test_real_defaults_to_five_runs(site):
    assert bringup(site, "real", "8").returncode == 0
    opts = json.loads(
        (
            site["chk"] / "local" / "nplb-work" / "real_ppn8" / "md_p2_a2a_C2_P1_ppn8.json"
        ).read_text()
    )["global_options"]
    assert (opts["minruns"], opts["maxruns"]) == ("5", "5")


# ---- status ----


def test_status_shows_the_queue_and_the_last_submits(site):
    work = site["chk"] / "local" / "nplb-work"
    work.mkdir(parents=True)
    (work / "run_log.tsv").write_text("".join(f"row{i}\n" for i in range(15)))
    result = bringup(site, "status")
    assert result.returncode == 0, result.stderr
    assert log_lines(site, "squeue") == ["squeue -p h100_4nodes"]
    assert "row14" in result.stdout and "row5" in result.stdout
    assert "row4" not in result.stdout


# ---- check ----


def bindings(dirpath: Path, lines: list[str], app: int = 0) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"bindings_app_{app}.log").write_text("\n".join(lines) + "\n")


def shim_lines(host_nics: dict[str, list[str]]) -> list[str]:
    return [
        f"crab: {host} local rank {rank} -> UCX_NET_DEVICES={nic}"
        for host, nics in host_nics.items()
        for rank, nic in enumerate(nics)
    ]


def test_check_ok_when_all_bound_and_each_rank_has_its_own_nic(site):
    run = site["data"] / "sys" / "smoke_md_p0_C2_P1_ppn8_2026"
    # two runs of the same app repeat the lines: they count once
    lines = shim_lines({"gn01": NICS, "gn02": NICS}) * 2
    bindings(run / "p0_cross_C2_P1", ["[h:1] MCW rank 0 bound to socket 0"] + lines)
    result = bringup(site, "check", str(run))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "p0_cross_C2_P1" in result.stdout
    assert "OK" in result.stdout and "CHECK" not in result.stdout.replace("CHECK:", "")
    assert "8 ranks, 8 NICs" in result.stdout


def test_check_flags_unbound_ranks_and_shared_nics(site):
    run = site["data"] / "sys" / "md_p1_C2_P1_ppn8_2026"
    good = shim_lines({"gn01": NICS, "gn02": NICS})
    bindings(run / "good", good)
    bindings(
        run / "unbound",
        ["[h:1] MCW rank 3 is not bound (or bound to all available processors)", *good],
    )
    shared = shim_lines({"gn01": NICS, "gn02": [*NICS[:7], NICS[0]]})
    bindings(run / "shared", shared)
    result = bringup(site, "check", str(run))
    assert result.returncode == 1
    by_name = {ln.split()[0]: ln for ln in result.stdout.splitlines() if ln.startswith("  ")}
    assert " OK " in by_name["good"]
    assert "CHECK" in by_name["unbound"] and "unbound 1" in by_name["unbound"]
    assert "CHECK" in by_name["shared"] and "gn02: 8 ranks, 7 NICs" in by_name["shared"]


def test_check_without_shim_lines_is_a_check(site):
    run = site["data"] / "sys" / "x_ppn8_2026"
    bindings(run / "exp", ["nothing useful"])
    (run / "empty").mkdir()
    result = bringup(site, "check", str(run))
    assert result.returncode == 1
    assert result.stdout.count("CHECK") >= 2


def test_check_defaults_to_the_run_log_and_shows_the_job_state(site):
    run = site["data"] / "sys" / "smoke_md_p0_C2_P1_ppn8_2026"
    bindings(run / "p0_cross_C2_P1", shim_lines({"gn01": NICS, "gn02": NICS}))
    work = site["chk"] / "local" / "nplb-work"
    work.mkdir(parents=True)
    (work / "run_log.tsv").write_text(
        f"2026-10-14T10:00:00\tC2\t1\t8\tcfg.json\t1001\t{run}\n"
        f"2026-10-14T10:00:01\tC2\t1\t8\tcfg2.json\t1002\t{site['data']}/gone\n"
    )
    result = bringup(site, "check", FAKE_STATE="RUNNING")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"== {run} (job 1001, state RUNNING)" in result.stdout
    assert "gone" in result.stdout  # reported as skipped, not a failure
    assert log_lines(site, "sacct") == ["sacct -j 1001 -X -n -P -o State"]


def test_check_falls_back_to_the_newest_ppn_dirs_under_the_data_root(site):
    run = site["data"] / "sys" / "smoke_md_p0_C2_P1_ppn8_2026"
    bindings(run / "p0_cross_C2_P1", shim_lines({"gn01": NICS, "gn02": NICS}))
    (site["data"] / "sys" / "other_2026").mkdir()
    result = bringup(site, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"== {run}" in result.stdout
    assert "other_2026" not in result.stdout


# ---- analyze ----


def make_data_dirs(site: dict) -> None:
    for name in (
        "smoke_md_p0_C2_P1_ppn8_2026-10-14",
        "smoke_md_p1_C2_P1_ppn8_2026-10-14",
        "smoke_md_p0_C2_P1_ppn16_2026-10-14",
        "smoke_md_p0_C2_P1_2026-10-14",
        "md_p2_a2a_C2_P1_ppn8_2026-10-14",
        "md_p2_a2a_C2_P1_ppn16_2026-10-14",
    ):
        (site["data"] / "sys" / name).mkdir(parents=True)


def test_analyze_smoke_uses_only_the_smoke_dirs_of_that_ppn(site):
    make_data_dirs(site)
    result = bringup(site, "analyze", "smoke", "8")
    assert result.returncode == 0, result.stderr
    base = site["data"] / "sys"
    assert log_lines(site, "analyze") == [
        f"analyze {base}/smoke_md_p0_C2_P1_ppn8_2026-10-14 {base}/smoke_md_p1_C2_P1_ppn8_2026-10-14"
    ]
    assert (site["chk"] / "local" / "nplb-work" / "results_smoke_ppn8" / "summary.md").exists()
    assert "summary line 60" in result.stdout
    assert "summary line 61" not in result.stdout


def test_analyze_real_uses_only_the_non_smoke_dirs_of_that_ppn(site):
    make_data_dirs(site)
    result = bringup(site, "analyze", "real", "8")
    assert result.returncode == 0, result.stderr
    base = site["data"] / "sys"
    assert log_lines(site, "analyze") == [f"analyze {base}/md_p2_a2a_C2_P1_ppn8_2026-10-14"]
    assert (site["chk"] / "local" / "nplb-work" / "results_real_ppn8" / "summary.md").exists()


def test_analyze_without_matching_dirs_fails(site):
    result = bringup(site, "analyze", "real", "8")
    assert result.returncode != 0
    assert "no data dirs" in result.stderr


# ---- topo ----


def test_topo_runs_topology_sh_and_passes_the_environment_through(site):
    result = bringup(site, "topo", PARTITION="other", WORK=str(site["root"] / "w"))
    assert result.returncode == 0, result.stderr
    assert log_lines(site, "topology") == [
        f"topology args= PARTITION=other WORK={site['root'] / 'w'}"
    ]
    assert "fake topology summary" in result.stdout
    assert f"+ {site['chk']}/examples/nplb/topology.sh" in result.stdout


def test_topo_passes_the_hosts_and_the_default_partition(site):
    result = bringup(site, "topo", "hostA", "hostB")
    assert result.returncode == 0, result.stderr
    assert log_lines(site, "topology") == [
        f"topology args=hostA hostB PARTITION=h100_4nodes WORK={site['chk']}/local/nplb-work"
    ]


@pytest.mark.parametrize("code", [1, 2])
def test_topo_exits_with_the_exit_code_of_topology_sh(site, code):
    result = bringup(site, "topo", FAKE_TOPO_RC=str(code))
    assert result.returncode == code
    assert "fake topology summary" in result.stdout


# ---- pingpong ----


def pingpong_dir(site: dict) -> Path:
    return site["chk"] / "local" / "nplb-work" / "pingpong"


def test_pingpong_generates_the_set_and_submits_its_four_jobs(site):
    out = pingpong_dir(site)
    out.mkdir(parents=True)
    (out / "pp_stale.json").write_text("{}")
    result = bringup(site, "pingpong")
    assert result.returncode == 0, result.stderr
    assert not (out / "pp_stale.json").exists()
    names = [
        "pp_n1n2_C2_P1.json",
        "pp_n1n3_C2_P1.json",
        "pp_n1n3_pw8_C2_P1.json",
        "pp_n1n4_C2_P1.json",
    ]
    assert sorted(p.name for p in out.glob("*.json")) == names
    chk = site["chk"]
    assert (
        f"+ {chk}/.venv/bin/python {chk}/examples/nplb/make_configs.py {chk}/profile8.json"
        f" --all-ports {chk}/profile16.json --set pingpong --out {out} --net C2 --paths 1"
        " --minruns 5 --maxruns 5 --iter-small 1000 --iter-128m 1000 --iter-512m 200"
    ) in result.stdout
    assert log_lines(site, "crab ") == [f"crab run -p nplb_h100 --json {out / n}" for n in names]
    rows = run_log(site)
    assert [r[3] for r in rows] == ["1", "1", "8", "1"]  # ppn: pairs 1, the node total 8
    for row, name, job in zip(rows, names, ("1001", "1002", "1003", "1004"), strict=True):
        assert row[1:3] == ["C2", "1"]
        assert row[4:6] == [str(out / name), job]
        assert row[6] == f"{site['data']}/sys/{Path(name).stem}_2026-10-14_10-00-00-000000"
    assert result.stdout.strip().splitlines()[-1].startswith("Submitted 4 ping-pong jobs")
    opts = json.loads((out / names[0]).read_text())["global_options"]
    assert (opts["minruns"], opts["maxruns"]) == ("5", "5")
    assert opts["name"] == "pp_n1n2_C2_P1"


def test_pingpong_honours_net_paths_and_runs(site):
    result = bringup(site, "pingpong", NET="C0", PATHS="2", RUNS="3")
    assert result.returncode == 0, result.stderr
    out = pingpong_dir(site)
    assert (out / "pp_n1n2_C0_P2.json").exists()
    assert "--net C0 --paths 2 --minruns 3 --maxruns 3" in result.stdout
    assert {(r[1], r[2]) for r in run_log(site)} == {("C0", "2")}


def test_pingpong_without_the_all_ports_profile_stops(site):
    (site["chk"] / "profile16.json").unlink()
    result = bringup(site, "pingpong")
    assert result.returncode == 1
    assert "profile16.json" in result.stderr
    assert log_lines(site, "crab ") == []


def test_pingpong_with_two_nets_fails_at_make_configs(site):
    result = bringup(site, "pingpong", NET="C0 C2")
    assert result.returncode == 1
    assert "step failed" in result.stderr and "make_configs.py" in result.stderr
    assert log_lines(site, "crab ") == []


# ---- analyze pingpong ----


def test_analyze_pingpong_uses_the_pp_dirs_and_prints_the_summary_head(site):
    base = site["data"] / "sys"
    for name in (
        "pp_n1n3_pw8_C2_P1_2026-10-14",
        "pp_n1n2_C2_P1_2026-10-14",
        "md_p2_a2a_C2_P1_ppn8_2026-10-14",
    ):
        (base / name).mkdir(parents=True)
    result = bringup(site, "analyze", "pingpong")
    assert result.returncode == 0, result.stderr
    assert log_lines(site, "analyze") == [
        f"analyze {base}/pp_n1n2_C2_P1_2026-10-14 {base}/pp_n1n3_pw8_C2_P1_2026-10-14"
    ]
    assert (site["chk"] / "local" / "nplb-work" / "results_pingpong" / "summary.md").exists()
    assert "summary line 60" in result.stdout
    assert "summary line 61" not in result.stdout


def test_analyze_pingpong_without_data_dirs_names_the_pattern(site):
    result = bringup(site, "analyze", "pingpong")
    assert result.returncode == 1
    assert f"no data dirs matching {site['data']}/*/pp_*" in result.stderr


# ---- usage and failures ----


def test_usage_lists_the_stage_subcommands(site):
    result = bringup(site, "bogus")
    assert result.returncode == 2
    for line in ("topo", "pingpong", "analyze pingpong"):
        assert line in result.stderr


@pytest.mark.parametrize(
    "args",
    [(), ("bogus",), ("nic", "12"), ("nic",), ("smoke",), ("real", "x"), ("analyze", "foo", "8")],
)
def test_bad_invocations_print_usage_and_exit_2(site, args):
    result = bringup(site, *args)
    assert result.returncode == 2
    assert "bringup.sh SUBCOMMAND" in result.stderr
    assert log_lines(site, "salloc") == [] and log_lines(site, "crab ") == []


def test_a_failing_crab_stops_the_script_with_a_message(site):
    result = bringup(site, "smoke", "8", FAKE_CRAB_FAIL="1")
    assert result.returncode != 0
    assert "step failed" in result.stderr
    assert len(log_lines(site, "crab ")) == 1  # the p1 and p2 files are not tried
    assert not (site["chk"] / "local" / "nplb-work" / "run_log.tsv").exists()


def test_a_missing_profile_stops_the_script(site):
    (site["chk"] / "profile8.json").unlink()
    result = bringup(site, "smoke", "8")
    assert result.returncode != 0
    assert "profile8.json" in result.stderr
    assert log_lines(site, "crab ") == []
