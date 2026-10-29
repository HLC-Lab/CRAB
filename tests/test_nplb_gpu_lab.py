"""examples/nplb/gpu_lab.sh (skeleton, `status`, `show`, `smoke`), run with bash against fakes.

The fakes are a per-host world for the real gpu_preflight.sh (fake ssh, sinfo, nvidia-smi, ldd,
ompi_info, findmnt, mlxreg and a fake sysfs), a fake `crab` that answers `validate` with chosen
text and exit code, and, for the screen-limit cases, a fake GPU_PREFLIGHT script. For `smoke` the
fake `crab` also answers `run --json` and `status --json` (a scripted sequence of states), the fake
GPU_PREFLIGHT answers `counters snapshot|diff`, and a canned run folder holds worker logs and CSVs
in the shape of a real run. Nothing real runs and nothing sends a signal; the script's WORK folder
is a tmp_path.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "gpu_lab.sh"
PORTS = [f"mlx5_{i}" for i in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)]
CHECKS = ["state", "ports", "gpus", "peermem", "mpirun", "ucx", "blink", "nfs", "sr"]
COUNTER_DIRS = {
    "np_ecn_marked_roce_packets": "hw_counters",
    "np_cnp_sent": "hw_counters",
    "rp_cnp_handled": "hw_counters",
    "port_xmit_data": "counters",
    "port_rcv_data": "counters",
}
SCREEN_LINES = 38
SCREEN_COLS = 100

# ---- the fake world of gpu_preflight.sh (copied from tests/test_nplb_gpu_preflight.py) ---------
FAKE_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "$host" >>"$FAKE_SSH_LOG"
dir=$FAKE_HOSTS_DIR/$host
if [[ -f $dir/unreachable ]]; then
    echo "ssh: connect to host $host port 22: No route to host" >&2
    exit 255
fi
cmd="$*"
cmd=${cmd//"$FAKE_HOSTS_DIR/head/"/"$dir/"}
unset LD_LIBRARY_PATH  # a real ssh does not carry it over
export FAKE_HOST_DIR=$dir
export SR_SYSFS=$dir/sysfs  # sr_state.sh reads /sys on the node; here the host's fake tree
source "$dir/env"
exec bash -c "$cmd"
"""
FAKE_SINFO_LOCAL = (
    '#!/usr/bin/env bash\necho "$*" >>"$FAKE_SSH_LOG.sinfo"\nprintf "w3\\nw1\\nhead\\nw2\\nw1\\n"\n'
)
FAKE_SINFO_HOST = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/state"\n'
FAKE_NVIDIA_SMI = '#!/usr/bin/env bash\n[[ $1 == -L ]] && cat "$FAKE_HOST_DIR/gpus.out"\n'
FAKE_LDD = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ldd.out"\n'
FAKE_FINDMNT = (
    "#!/usr/bin/env bash\n"
    "if [[ $* == *SOURCE* ]]; then f=source; else f=fstype; fi\n"
    '[[ -f $FAKE_HOST_DIR/$f ]] && cat "$FAKE_HOST_DIR/$f"\nexit 0\n'
)
FAKE_MLXREG = r"""#!/usr/bin/env bash
f=$FAKE_HOST_DIR/sr.$2
[[ -f $f ]] || f=$FAKE_HOST_DIR/sr.default
printf 'selective_repeat_forced_en | 0x%08x\n' "$(<"$f")"
printf 'adaptive_routing_forced_en | 0x00000000\n'
"""
FAKE_OMPI_INFO = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ompi.out"\n'
FAKE_EXE = "#!/usr/bin/env bash\nexit 0\n"
LDD_OK = "\tlibucp.so.0 => /lab/ucx-cuda/lib/libucp.so.0 (0x1)\n\tlibcudart.so.12 => /cuda/libcudart.so.12 (0x2)\n"

# `crab validate`: prints $FAKE_CRAB_DIR/validate.out (default "ok: <file>"), exits with validate.rc
FAKE_CRAB = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_CRAB_DIR/calls"
if [[ $1 == validate ]]; then
    if [[ -f $FAKE_CRAB_DIR/validate.out ]]; then cat "$FAKE_CRAB_DIR/validate.out"; else echo "ok: ${!#}"; fi
    [[ -f $FAKE_CRAB_DIR/validate.rc ]] && exit "$(<"$FAKE_CRAB_DIR/validate.rc")"
    exit 0
fi
exit 3
"""

# A stand-in for gpu_preflight.sh: records its arguments, then prints OK rows (or, with
# FAKE_PREFLIGHT_MODE=fail, FAIL rows with 300-character details) for every host it was given.
FAKE_PREFLIGHT = r"""#!/usr/bin/env bash
echo "$*" >"$FAKE_PREFLIGHT_ARGS"
printf -v long '%0300d' 0
fails=0
for h in "$@"; do
    for c in state ports gpus peermem mpirun ucx blink nfs sr; do
        if [[ ${FAKE_PREFLIGHT_MODE:-ok} == fail ]]; then
            echo "$h $c FAIL detail-$c-$long-END"
            fails=$((fails + 1))
        else
            echo "$h $c OK fine"
        fi
    done
done
echo "gpu_preflight: $fails FAIL, 0 WARN on $# hosts"
((fails == 0))
"""

# The only real tools on PATH: the machine's own sinfo, nvidia-smi, ... must not leak in.
REAL_TOOLS = (
    "bash sh env cat sed awk sort uname rm mkdir mktemp dirname tail head grep tr cut wc readlink "
    "basename git date ln ls uniq"
).split()


def put(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


def counter_file(host: Path, dev: str, name: str) -> Path:
    return host / "sysfs" / "class" / "infiniband" / dev / "ports" / "1" / COUNTER_DIRS[name] / name


def build_host(root: Path, name: str, base_path: str) -> Path:
    """One host with everything healthy: 16 ACTIVE ports, 8 GPUs, the lab stack, an NFS checkout."""
    d = root / "hosts" / name
    ib = d / "sysfs" / "class" / "infiniband"
    for dev in [*PORTS, "mlx5_6", "mlx5_7"]:
        state = "1: DOWN\n" if dev in ("mlx5_6", "mlx5_7") else "4: ACTIVE\n"
        put(ib / dev / "ports" / "1" / "state", state)
        (d / "pci" / f"pci-{dev}").mkdir(parents=True)
        (ib / dev / "device").symlink_to(d / "pci" / f"pci-{dev}")
        for counter in COUNTER_DIRS:
            put(counter_file(d, dev, counter), "1000\n")
    put(d / "proc" / "modules", "nvidia 1 0 - Live 0x0\nnvidia_peermem 16384 0 - Live 0x0\n")
    lab = d / "lab"
    put(lab / "ompi-cuda" / "bin" / "mpirun", FAKE_EXE, 0o755)
    put(lab / "ompi-cuda" / "bin" / "ompi_info", FAKE_OMPI_INFO, 0o755)
    (lab / "ompi-cuda" / "lib").mkdir(parents=True)
    put(lab / "ucx-cuda" / "lib" / "libucp.so.0", "")
    for prog in ("alltoall_comm_only", "allgather_comm_only"):
        put(lab / "blink-gpu" / "build-cuda" / "bin" / prog, FAKE_EXE, 0o755)
    (d / "cudalib").mkdir()
    put(
        d / "gpus.out",
        "".join(f"GPU {i}: NVIDIA H100 80GB HBM3 (UUID: GPU-{i})\n" for i in range(8)),
    )
    put(d / "state", "idle\n")
    put(d / "ompi.out", "mca:mpi:base:param:mpi_built_with_cuda_support:value:true\n")
    put(d / "ldd.out", LDD_OK)
    put(d / "fstype", "nfs4\n")
    put(d / "source", "head:/root/crab\n")
    put(d / "sr.default", "1\n")
    for tool, text in (
        ("nvidia-smi", FAKE_NVIDIA_SMI),
        ("sinfo", FAKE_SINFO_HOST),
        ("ldd", FAKE_LDD),
        ("findmnt", FAKE_FINDMNT),
        ("mlxreg", FAKE_MLXREG),
    ):
        put(d / "bin" / tool, text, 0o755)
    put(d / "env", f'export PATH="{d}/bin:{base_path}"\n')
    return d


def make_world(root: Path, hosts: list[str]) -> dict:
    tools = root / "realbin"
    tools.mkdir()
    for tool in REAL_TOOLS:
        found = shutil.which(tool)
        if found:
            (tools / tool).symlink_to(found)
    gbin = root / "gbin"
    put(gbin / "ssh", FAKE_SSH, 0o755)
    put(gbin / "sinfo", FAKE_SINFO_LOCAL, 0o755)
    put(gbin / "crab", FAKE_CRAB, 0o755)
    presets = root / "presets"
    put(presets / "nplb_h100_gpu.json", '{"nplb_h100_gpu": {"scheduler": "slurm"}}\n')
    (root / "crabdir").mkdir()
    built = {n: build_host(root, n, str(tools)) for n in hosts}
    return {
        "root": root,
        "gbin": gbin,
        "hosts": built,
        "base": str(tools),
        "presets": presets,
        "work": root / "work",
        "crabdir": root / "crabdir",
    }


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path, ["head", "w1", "w2"])


def run_lab(world: dict, args: list, **env) -> subprocess.CompletedProcess:
    head = world["hosts"].get("head") or next(iter(world["hosts"].values()))
    full_env = {
        **os.environ,
        "PATH": f"{world['gbin']}:{world['base']}",
        "FAKE_HOSTS_DIR": str(world["root"] / "hosts"),
        "FAKE_SSH_LOG": str(world["root"] / "ssh.log"),
        "FAKE_CRAB_DIR": str(world["crabdir"]),
        "FAKE_PREFLIGHT_ARGS": str(world["root"] / "preflight.args"),
        "PREFLIGHT_SSH": str(world["gbin"] / "ssh"),
        "CRAB": str(world["gbin"] / "crab"),
        "CRAB_PRESETS_DIR": str(world["presets"]),
        "WORK": str(world["work"]),
        "LAB": str(head / "lab"),
        "CUDA_LIB": str(head / "cudalib"),
        "HEAD": "head",
        "GPU_PREFLIGHT_SYSFS": str(head / "sysfs"),
        "GPU_PREFLIGHT_PROC": str(head / "proc"),
        "PYTHON": sys.executable,  # the test PATH has no python3
        "FAKE_ORDER": str(world["root"] / "order.log"),
    }
    for name in ("PARTITION", "CRAB_PATH_PRESETS", "GPU_PREFLIGHT", "SMOKE_JSON", "NODES"):
        full_env.pop(name, None)
    full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=120
    )


def use_fake_preflight(world: dict, **env) -> dict:
    path = world["root"] / "fake_preflight.sh"
    put(path, FAKE_PREFLIGHT, 0o755)
    return {"GPU_PREFLIGHT": str(path), **env}


def screen_limits(out: str) -> None:
    lines = out.splitlines()
    assert len(lines) <= SCREEN_LINES, f"{len(lines)} lines:\n{out}"
    assert max(len(ln) for ln in lines) <= SCREEN_COLS, out


def grid(out: str) -> tuple[list, dict]:
    """(check names, {host: {check: cell}}) from the grid whose header line starts with `node`."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.split()[:1] == ["node"])
    header = lines[start].split()[1:]
    rows = {}
    for ln in lines[start + 1 :]:
        if not ln.strip():
            break
        host, *cells = ln.split()
        rows[host] = dict(zip(header, cells, strict=True))
    return header, rows


def below_grid(out: str) -> list[str]:
    """The lines between the grid and the verdict line, without blank lines."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.split()[:1] == ["node"])
    end = start + 1
    while lines[end].strip():
        end += 1
    return [ln for ln in lines[end:] if ln.strip() and not ln.startswith("status:")]


def runs(world: dict) -> list[Path]:
    base = world["work"] / "gpu_lab"
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink())


# ---- a healthy world, run once and read by several tests -------------------------------------


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    w = make_world(tmp_path_factory.mktemp("healthy"), ["head", "w1", "w2"])

    def tree(root: Path) -> dict:
        return {str(p.relative_to(root)): p.read_text() for p in root.rglob("*") if p.is_file()}

    before = {n: tree(h) for n, h in w["hosts"].items()}
    result = run_lab(w, ["status", "head", "w1", "w2"])
    return w, result, before, {n: tree(h) for n, h in w["hosts"].items()}


def test_script_is_executable_and_parses():
    assert os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_healthy_status_screen_has_every_section_and_exits_0(healthy):
    w, result, _, _ = healthy
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    lines = out.splitlines()
    screen_limits(out)
    assert lines[0].startswith("gpu_lab status")
    assert re.search(r"^checkout\s+OK\s+\S+ \S+", out, re.M)
    assert re.search(r"^preset\s+OK\s+\S*nplb_h100_gpu\.json", out, re.M)
    for what, name in (
        ("smoke", "smoke_gpu_a2a_comm.json"),
        ("a2a", "gpu_a2a_comm.json"),
        ("allgather", "gpu_ag_comm.json"),
    ):
        assert re.search(rf"^validate {what}\s+OK\s+{re.escape(name)}$", out, re.M), out
    header, rows = grid(out)
    assert header == CHECKS
    assert list(rows) == ["head", "w1", "w2"]
    for host in ("w1", "w2"):
        assert set(rows[host].values()) == {"ok"}
    assert rows["head"]["nfs"] == "-"
    assert {v for k, v in rows["head"].items() if k != "nfs"} == {"ok"}
    assert lines[-1] == "status: 0 FAIL, 0 WARN"
    assert below_grid(out) == []


def test_validate_runs_crab_with_the_preset_on_each_committed_file(healthy):
    w, _, _, _ = healthy
    calls = (w["crabdir"] / "calls").read_text().splitlines()
    assert len(calls) == 3
    for call, name in zip(
        calls, ("smoke_gpu_a2a_comm", "gpu_a2a_comm", "gpu_ag_comm"), strict=True
    ):
        assert call.startswith("validate -p nplb_h100_gpu ")
        assert call.endswith(f"/examples/nplb/h100/{name}.json")


def test_status_writes_only_under_work_and_changes_no_node(healthy):
    w, _, before, after = healthy
    assert after == before
    assert [p.name for p in w["work"].iterdir()] == ["gpu_lab"]


def test_the_log_holds_the_full_preflight_and_validate_output(healthy):
    w, _, _, _ = healthy
    (run,) = runs(w)
    log = (run / "status.log").read_text()
    assert re.search(r"^head\s+ports\s+OK\s+16 ports ACTIVE$", log, re.M)
    assert re.search(r"^w2\s+sr\s+OK\s+SR=1 AR=0 on 16 devices$", log, re.M)
    assert "gpu_preflight: 0 FAIL, 0 WARN on 3 hosts" in log
    assert "ok: " in log and "gpu_ag_comm.json" in log
    assert (w["work"] / "gpu_lab" / "latest").resolve() == run.resolve()


def test_show_status_prints_the_log_of_the_last_run(healthy):
    w, _, _, _ = healthy
    result = run_lab(w, ["show", "status"])
    (run,) = runs(w)
    assert result.returncode == 0
    assert result.stdout == (run / "status.log").read_text()


def test_show_an_unknown_step_or_a_step_without_a_log_exits_2_with_one_line(healthy):
    w, _, _, _ = healthy
    for step in ("nonexistent", "smoke", "../status"):
        result = run_lab(w, ["show", step])
        assert result.returncode == 2
        assert result.stdout == ""
        assert len(result.stderr.strip().splitlines()) == 1, result.stderr
    result = run_lab(w, ["show"])
    assert result.returncode == 2
    assert len(result.stderr.strip().splitlines()) == 1


def test_show_with_no_run_yet_exits_2_with_one_line(world):
    result = run_lab(world, ["show", "status"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert not world["work"].exists()


def test_every_run_gets_its_own_folder_and_latest_follows_the_newest(world):
    env = use_fake_preflight(world)
    assert run_lab(world, ["status", "head"], **env).returncode == 0
    first = runs(world)
    assert len(first) == 1
    assert run_lab(world, ["status", "head"], **env).returncode == 0
    both = runs(world)
    assert len(both) == 2
    (newest,) = set(both) - set(first)
    assert (world["work"] / "gpu_lab" / "latest").resolve() == newest.resolve()
    assert (newest / "status.log").is_file() and (first[0] / "status.log").is_file()


# ---- cell values -----------------------------------------------------------------------------


def test_a_down_port_makes_exactly_that_cell_fail_and_a_warning_shows_as_warn(world):
    w1 = world["hosts"]["w1"]
    (counter_file(w1, "mlx5_3", "np_cnp_sent").parents[1] / "state").write_text("1: DOWN\n")
    put(
        world["hosts"]["w2"]
        / "sysfs"
        / "class"
        / "infiniband"
        / "mlx5_18"
        / "ports"
        / "1"
        / "state",
        "4: ACTIVE\n",
    )
    result = run_lab(world, ["status", "head", "w1", "w2"])
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    screen_limits(out)
    _, rows = grid(out)
    assert rows["w1"]["ports"] == "FAIL"
    assert rows["w2"]["ports"] == "WARN"
    others = {
        (h, c): v
        for h, cells in rows.items()
        for c, v in cells.items()
        if (h, c) not in {("w1", "ports"), ("w2", "ports"), ("head", "nfs")}
    }
    assert set(others.values()) == {"ok"}
    detail = {tuple(ln.split(None, 2)[:2]): ln.split(None, 2)[2] for ln in below_grid(out)}
    assert "mlx5_3" in detail[("w1", "ports")]
    assert "mlx5_18" in detail[("w2", "ports")]
    assert len(detail) == 2
    assert out.splitlines()[-1] == "status: 1 FAIL, 1 WARN"


def test_an_unreachable_host_gets_an_ssh_cell_and_dashes(world):
    (world["hosts"]["w1"] / "unreachable").write_text("")
    result = run_lab(world, ["status", "head", "w1"])
    assert result.returncode == 1
    header, rows = grid(result.stdout)
    assert header[-1] == "ssh"
    assert rows["w1"]["ssh"] == "FAIL"
    assert {v for k, v in rows["w1"].items() if k != "ssh"} == {"-"}
    assert rows["head"]["ssh"] == "-"
    assert any("No route to host" in ln for ln in below_grid(result.stdout))
    assert result.stdout.splitlines()[-1] == "status: 1 FAIL, 0 WARN"


def test_preflight_failing_to_start_shows_one_preflight_line_and_status_goes_on(world):
    bad = world["root"] / "bad_preflight.sh"
    put(bad, "#!/usr/bin/env bash\necho 'gpu_preflight: no way' >&2\nexit 2\n", 0o755)
    result = run_lab(world, ["status", "head"], GPU_PREFLIGHT=str(bad))
    assert result.returncode == 1
    assert re.search(r"^preflight\s+FAIL\s+gpu_preflight: no way$", result.stdout, re.M)
    assert re.search(r"^validate smoke\s+OK", result.stdout, re.M)
    assert result.stdout.splitlines()[-1] == "status: 1 FAIL, 0 WARN"
    assert "gpu_preflight: no way" in (runs(world)[0] / "status.log").read_text()


# ---- preset ----------------------------------------------------------------------------------


def test_a_missing_preset_fails_with_the_copy_command(world):
    shutil.rmtree(world["presets"])
    world["presets"].mkdir()
    result = run_lab(world, ["status", "head"], **use_fake_preflight(world))
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^preset\s+FAIL\s+no \*\.json in ", out, re.M), out
    fix = f"  fix: cp examples/nplb/presets/nplb_h100_gpu.json {world['presets']}/"
    assert any(ln.startswith(fix[:70]) for ln in out.splitlines()), out
    log = (runs(world)[0] / "status.log").read_text()
    assert f"{fix}   (mkdir -p {world['presets']} first if it is missing)" in log
    assert "status: 1 FAIL, 0 WARN" in out


def test_the_preset_is_found_in_any_json_file_of_the_folder(world):
    (world["presets"] / "nplb_h100_gpu.json").rename(world["presets"] / "mine.json")
    result = run_lab(world, ["status", "head"], **use_fake_preflight(world))
    assert result.returncode == 0, result.stdout
    assert re.search(r"^preset\s+OK\s+\S*mine\.json", result.stdout, re.M)


def test_a_json_file_without_the_preset_does_not_count(world):
    put(world["presets"] / "nplb_h100_gpu.json", '{"other": {}}\n')
    result = run_lab(world, ["status", "head"], **use_fake_preflight(world))
    assert re.search(r"^preset\s+FAIL", result.stdout, re.M)


def test_crab_path_presets_names_the_folder_when_set(world):
    empty = world["root"] / "empty"
    empty.mkdir()
    env = use_fake_preflight(world, CRAB_PRESETS_DIR="", CRAB_PATH_PRESETS=str(empty))
    assert re.search(r"^preset\s+FAIL", run_lab(world, ["status", "head"], **env).stdout, re.M)
    env["CRAB_PATH_PRESETS"] = str(world["presets"])
    assert re.search(r"^preset\s+OK", run_lab(world, ["status", "head"], **env).stdout, re.M)


def test_the_preset_falls_back_to_the_user_config_folder(world):
    xdg = world["root"] / "xdg"
    put(xdg / "crab" / "presets" / "p.json", '{"nplb_h100_gpu": {}}\n')
    env = use_fake_preflight(world, CRAB_PRESETS_DIR="", XDG_CONFIG_HOME=str(xdg))
    result = run_lab(world, ["status", "head"], **env)
    assert re.search(r"^preset\s+OK", result.stdout, re.M), result.stdout


# ---- crab validate ---------------------------------------------------------------------------


def test_a_failing_validate_shows_its_first_line_and_status_goes_on(world):
    put(world["crabdir"] / "validate.out", "\ncrab: bad config: no apps\nsecond line\n")
    put(world["crabdir"] / "validate.rc", "1\n")
    result = run_lab(world, ["status", "head", "w1"])
    assert result.returncode == 1
    out = result.stdout
    for what in ("smoke", "a2a", "allgather"):
        assert re.search(rf"^validate {what}\s+FAIL\s+crab: bad config: no apps$", out, re.M), out
    assert "second line" not in out
    header, rows = grid(out)
    assert set(rows) == {"head", "w1"} and header == CHECKS
    assert out.splitlines()[-1] == "status: 3 FAIL, 0 WARN"
    assert "second line" in (runs(world)[0] / "status.log").read_text()


def test_a_missing_crab_fails_the_validate_lines(world):
    result = run_lab(world, ["status", "head"], CRAB=str(world["root"] / "nope"))
    assert result.returncode == 1
    assert len(re.findall(r"^validate \w+\s+FAIL\s+.*crab", result.stdout, re.M)) == 3


# ---- the screen limit ------------------------------------------------------------------------


def test_every_check_failing_with_long_details_stays_within_the_screen(world):
    put(
        world["crabdir"] / "validate.out",
        "".join(f"crab: error line {i} " + "e" * 150 + "\n" for i in range(1, 31)),
    )
    put(world["crabdir"] / "validate.rc", "1\n")
    env = use_fake_preflight(world, FAKE_PREFLIGHT_MODE="fail")
    result = run_lab(world, ["status", "h1", "h2", "h3", "h4"], **env)
    assert result.returncode == 1
    out = result.stdout
    screen_limits(out)
    assert "~" in out
    assert out.splitlines()[-1] == "status: 39 FAIL, 0 WARN"
    log = (runs(world)[0] / "status.log").read_text()
    assert f"detail-sr-{'0' * 300}-END" in log
    assert "crab: error line 30 " in log and "crab: error line 30 " not in out


def test_too_many_lines_are_cut_and_the_last_line_says_how_many(world):
    hosts = [f"h{i:02d}" for i in range(1, 21)]
    env = use_fake_preflight(world, FAKE_PREFLIGHT_MODE="fail")
    result = run_lab(world, ["status", *hosts], **env)
    assert result.returncode == 1
    out = result.stdout
    lines = out.splitlines()
    screen_limits(out)
    assert len(lines) == SCREEN_LINES
    m = re.fullmatch(r"\.\.\. (\d+) more lines, see: gpu_lab\.sh show status", lines[-1])
    assert m, lines[-1]
    assert int(m.group(1)) >= 1
    log = (runs(world)[0] / "status.log").read_text()
    assert "status: 180 FAIL, 0 WARN" in log
    assert "h20 sr FAIL detail-sr-" in log


def test_four_long_host_names_stay_within_the_screen_and_are_shortened_in_the_grid(tmp_path):
    names = [f"node-with-a-really-long-host-name-{i}.some.cluster.example.org" for i in range(4)]
    w = make_world(tmp_path, ["head", *names])
    (counter_file(w["hosts"][names[2]], "mlx5_3", "np_cnp_sent").parents[1] / "state").write_text(
        "1: DOWN\n"
    )
    result = run_lab(w, ["status", *names])
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    screen_limits(out)
    header, rows = grid(out)
    assert header == CHECKS
    # the domain goes first, then the middle: the node number at the end still tells them apart
    assert list(rows) == [f"node-with-a-~host-name-{i}" for i in range(4)]
    assert sorted(v for cells in rows.values() for v in cells.values()).count("FAIL") == 1
    assert rows["node-with-a-~host-name-2"]["ports"] == "FAIL"
    assert out.splitlines()[-1] == "status: 1 FAIL, 0 WARN"
    # the log keeps the full host names
    log = (runs(w)[0] / "status.log").read_text()
    assert re.search(rf"^{re.escape(names[2])}\s+ports\s+FAIL\s", log, re.M)


def test_a_long_details_list_is_capped_with_a_pointer_to_the_log(world):
    env = use_fake_preflight(world, FAKE_PREFLIGHT_MODE="fail")
    result = run_lab(world, ["status", "h1", "h2"], **env)
    lines = below_grid(result.stdout)
    assert len(lines) == 13
    assert re.fullmatch(r"\.\.\. 6 more .*gpu_lab\.sh show status", lines[-1]), lines[-1]


# ---- usage and hosts -------------------------------------------------------------------------


def test_help_prints_usage_on_stdout_and_exits_0(world):
    for flag in ("-h", "--help"):
        result = run_lab(world, [flag])
        assert result.returncode == 0
        assert "gpu_lab.sh status" in result.stdout and "gpu_lab.sh show STEP" in result.stdout
        assert "CRAB_PRESETS_DIR" in result.stdout and "PREFLIGHT_SSH" in result.stdout
    assert not world["work"].exists()


def test_no_arguments_prints_usage_on_stderr_and_exits_2(world):
    result = run_lab(world, [])
    assert result.returncode == 2
    assert result.stdout == ""
    assert "gpu_lab.sh status" in result.stderr
    assert not world["work"].exists()


def test_an_unknown_subcommand_exits_2(world):
    result = run_lab(world, ["frobnicate"])
    assert result.returncode == 2
    assert "frobnicate" in result.stderr
    assert not world["work"].exists()


@pytest.mark.parametrize("step", ["pin", "all"])
def test_steps_not_built_yet_say_so_and_exit_2(world, step):
    result = run_lab(world, [step])
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == f"gpu_lab: step '{step}' is not available yet"
    assert not world["work"].exists()


def test_status_uses_the_hosts_given_in_that_order_and_does_not_call_sinfo(world):
    result = run_lab(world, ["status", "w2", "head"], **use_fake_preflight(world))
    assert result.returncode == 0
    assert (world["root"] / "preflight.args").read_text().split() == ["w2", "head"]
    assert not (world["root"] / "ssh.log.sinfo").exists()
    assert list(grid(result.stdout)[1]) == ["w2", "head"]


def test_status_without_hosts_lists_the_nodes_of_the_partition(world):
    result = run_lab(world, ["status"], PARTITION="p", **use_fake_preflight(world))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (world["root"] / "preflight.args").read_text().split() == ["head", "w1", "w2", "w3"]
    assert "-p p" in (world["root"] / "ssh.log.sinfo").read_text()


def test_status_without_hosts_defaults_to_the_h100_partition(world):
    run_lab(world, ["status"], **use_fake_preflight(world))
    assert "-p h100_4nodes" in (world["root"] / "ssh.log.sinfo").read_text()


def test_no_hosts_and_no_nodes_in_the_partition_exits_2_with_one_line(world):
    put(world["gbin"] / "sinfo", "#!/usr/bin/env bash\nexit 0\n", 0o755)
    result = run_lab(world, ["status"], **use_fake_preflight(world))
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert not (world["root"] / "preflight.args").exists()


def test_no_hosts_and_no_sinfo_exits_2_with_one_line(world):
    (world["gbin"] / "sinfo").unlink()
    result = run_lab(world, ["status"], **use_fake_preflight(world))
    assert result.returncode == 2
    assert len(result.stderr.strip().splitlines()) == 1


# ---- read-only guard -------------------------------------------------------------------------
# Commands that change a node or a job. The script may write only under $WORK.
WRITE_PATTERNS = {
    "mlxreg -s": r"mlxreg\s+-s\b",
    "mlxconfig": r"mlxconfig",
    "sr_set": r"sr_set",
    "tee into /sys or /proc": r"\btee\b.*(/sys|/proc|\$\{?(sysfs|SYSFS|proc|PROC)\b)",
    "redirect into /sys or /proc": r">>?\s*[\"']?(/sys|/proc|\$\{?(sysfs|SYSFS|proc|PROC)\b)",
    "modprobe": r"\b(modprobe|insmod|rmmod)\b",
    "umount": r"umount",
    "systemctl": r"systemctl",
    "kill": r"kill",
    "scancel": r"scancel",
}


@pytest.mark.parametrize("name", WRITE_PATTERNS)
def test_the_script_holds_no_command_that_writes_to_a_node(name):
    hits = [
        ln
        for ln in SCRIPT.read_text().splitlines()
        if re.search(WRITE_PATTERNS[name], ln, re.IGNORECASE)
    ]
    assert hits == []


@pytest.mark.parametrize(
    "bad",
    [
        "mlxreg -s -d x",
        "mlxconfig -d x set",
        "examples/nplb/sr_set.sh 1 a",
        "echo 1 | tee /sys/x",
        'echo 1 | tee "$sysfs/x"',
        'echo 1 > "$sysfs/x"',
        "echo 1 >/proc/sys/x",
        "modprobe nvidia",
        "x && umount /y",
        "systemctl stop x",
        "kill -9 1",
        "scancel 12",
    ],
)
def test_the_read_only_guard_does_catch_the_bad_commands(bad):
    assert any(re.search(p, bad, re.IGNORECASE) for p in WRITE_PATTERNS.values())


# ---- smoke -----------------------------------------------------------------------------------
REPO = SCRIPT.parents[2]
SMOKE_JSON = REPO / "examples" / "nplb" / "h100" / "smoke_gpu_a2a_comm.json"
AG_SMOKE_JSON = REPO / "examples" / "nplb" / "h100" / "smoke_gpu_ag_comm.json"
SMOKE_EXPS = ["ppn1_alone", "ppn1_vs_a2a", "ppn8_alone", "ppn8_vs_a2a"]
SIZE_SMALL = 1048576
SIZE_LARGE = 67108864
SMOKE_POINTER = "gpu_lab.sh show smoke"

# `crab`: validate as before; `run --json` prints run.out (stdout) and run.err (stderr), exits with
# run.rc, and records the directory it ran in; `status --json JOBID` answers the next state of the
# `states` file (the last one repeats); the state NOSTATUS makes the call itself fail.
FAKE_CRAB_SMOKE = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_CRAB_DIR/calls"
echo "crab $*" >>"$FAKE_ORDER"
d=$FAKE_CRAB_DIR
case $1 in
validate)
    if [[ -f $d/validate.out ]]; then cat "$d/validate.out"; else echo "ok: ${!#}"; fi
    [[ -f $d/validate.rc ]] && exit "$(<"$d/validate.rc")"
    exit 0
    ;;
run)
    pwd >"$d/run.pwd"
    [[ -f $d/run.err ]] && cat "$d/run.err" >&2
    [[ -f $d/run.out ]] && cat "$d/run.out"
    [[ -f $d/run.rc ]] && exit "$(<"$d/run.rc")"
    exit 0
    ;;
status)
    n=0
    [[ -f $d/status.n ]] && n=$(<"$d/status.n")
    n=$((n + 1))
    echo "$n" >"$d/status.n"
    state=$(sed -n "${n}p" "$d/states")
    [[ -n $state ]] || state=$(tail -n 1 "$d/states")
    if [[ $state == NOSTATUS ]]; then echo "crab: no scheduler here" >&2; exit 1; fi
    printf '{\n  "schema": 1,\n  "jobs": [\n    {\n      "job_id": "%s",\n' "${!#}"
    printf '      "state": "%s",\n      "source": "squeue"\n    }\n  ]\n}\n' "$state"
    exit 0
    ;;
esac
exit 3
"""

# `gpu_preflight.sh counters snapshot|diff`: records its call in the shared order log. A snapshot
# writes the file (snap.rc non-zero: complains on stderr and exits with it); a diff prints diff.out
# (a default of one moved counter and the three summary lines) and exits with diff.rc, writing
# diff.err on stderr.
FAKE_PREFLIGHT_SMOKE = r"""#!/usr/bin/env bash
echo "preflight $*" >>"$FAKE_ORDER"
d=$FAKE_CRAB_DIR
[[ $1 == counters ]] || exit 9
if [[ $2 == snapshot ]]; then
    if [[ -f $d/snap.rc ]]; then
        echo "w2 ssh FAIL ssh: connect to host w2 port 22: No route to host" >&2
        exit "$(<"$d/snap.rc")"
    fi
    echo "w1 mlx5_0 port_xmit_data 1000" >"$3"
    echo "gpu_preflight: wrote 1 counter lines for 1 of 1 hosts to $3"
    exit 0
fi
[[ -f $d/diff.err ]] && cat "$d/diff.err" >&2
if [[ -f $d/diff.out ]]; then
    cat "$d/diff.out"
else
    echo "w1   mlx5_0   port_xmit_data           +4096"
    echo "ports with traffic: w1/mlx5_0 w2/mlx5_0"
    echo "ECN/CNP activity: no"
    echo "proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch"
fi
[[ -f $d/diff.rc ]] && exit "$(<"$d/diff.rc")"
exit 0
"""

DIFF_LINES = [
    "ports with traffic: w1/mlx5_0 w2/mlx5_0",
    "ECN/CNP activity: no",
    "proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch",
]


def load_smoke_suite(path: Path = SMOKE_JSON) -> dict:
    return json.loads(path.read_text())


def victim_apps(suite: dict, exp: str) -> list[tuple[str, int]]:
    """(app id, message size) of the apps of `exp` that collect data, in id order."""
    apps = suite["experiments"][exp]["apps"]
    found = []
    for aid, app in apps.items():
        if app.get("collect") is True:
            found.append((aid, int(re.search(r"-msgsize (\d+)", app["args"]).group(1))))
    return found


def log_line(t: int, exp: str, app: str, message: str) -> str:
    """One worker-log line in the colored shape of a real run (separators are U+2502)."""
    h, m, s = t // 3600 % 24, t // 60 % 60, t % 60
    dim = "\x1b[2m"
    off = "\x1b[0m"
    return (
        f"{dim}[{h:02d}:{m:02d}:{s:02d}]{off} \x1b[32mINFO{off} \x1b[34mCRAB{off} "
        f"{dim}\u2502{off} {dim}{exp}{off} {dim}\u2502{off} {dim}Run 1{off} {dim}\u2502{off} "
        f"{dim}App {app}{off}  {message}\n"
    )


def plain_line(t: int, message: str) -> str:
    """A worker-log line without the experiment prefix."""
    h, m, s = t // 3600 % 24, t // 60 % 60, t % 60
    return f"\x1b[2m[{h:02d}:{m:02d}:{s:02d}]\x1b[0m \x1b[32mINFO\x1b[0m \x1b[34mCRAB\x1b[0m  {message}\n"


def build_run(
    root: Path,
    suite: dict | None = None,
    *,
    median=1e-3,
    rows: int = 5,
    wall: int = 12,
    start: int = 10 * 3600,
    elapsed: str = "0:01:35",
    skip_csv=(),
    empty=(),
    no_finish=(),
    failed=(),
    no_log=(),
    errors: dict | None = None,
    slurm_error: str = "",
) -> Path:
    """A run folder in the shape of a real one: slurm_output.log, slurm_error.log, per-experiment
    data_app_<id>.csv of the victim apps. `median` is a number or f(exp, app, size) in seconds;
    skip_csv, empty, no_finish, failed and no_log are sets of (experiment, app id)."""
    suite = suite or load_smoke_suite()
    root.mkdir(parents=True)
    log = [plain_line(start, "Worker started")]
    t = start
    for exp, spec in suite["experiments"].items():
        exp_dir = root / exp
        exp_dir.mkdir()
        if any(a.get("collect") is not True for a in spec["apps"].values()):
            aggressor = next(i for i, a in spec["apps"].items() if a.get("collect") is not True)
            log.append(log_line(t, exp, aggressor, f"Launched App {aggressor}  PID=137"))
        for aid, size in victim_apps(suite, exp):
            key = (exp, aid)
            if key not in no_log:
                log.append(log_line(t, exp, aid, f"Launched App {aid}  PID=164  nodes=['n1']"))
            if key not in no_log and key not in no_finish:
                if key in failed:
                    log.append(log_line(t + wall, exp, aid, "FAILED  exit=1"))
                else:
                    log.append(log_line(t + wall, exp, aid, "FINISHED  exit=0"))
            t += wall
            if key in skip_csv:
                continue
            m = median(exp, aid, size) if callable(median) else median
            head = f"run_id,msg_size,{aid}_Avg-Duration_s,{aid}_Min-Duration_s,"
            head += f"{aid}_Max-Duration_s,{aid}_Median-Duration_s,{aid}_MainRank-Duration_s\n"
            n = 0 if key in empty else rows
            body = "".join(f"1,{size},{m!r},{m!r},{m!r},{m!r},{m!r}\n" for _ in range(n))
            (exp_dir / f"data_app_{aid}.csv").write_text(head + body)
    log.append(plain_line(t, f"Engine run finished  elapsed={elapsed}"))
    (root / "slurm_output.log").write_text("".join(log))
    (root / "slurm_error.log").write_text(slurm_error)
    for (exp, aid), text in (errors or {}).items():
        (root / exp / f"error_app_{aid}.log").write_text(text)
    return root


def smoke_env(
    world: dict, *, states: str = "PENDING\nRUNNING\nCOMPLETED\n", suite=None, **run_kw
) -> dict:
    """The fakes of a healthy smoke: fake crab, a run folder, the fake preflight. Returns env."""
    root = world["root"]
    put(world["gbin"] / "crab", FAKE_CRAB_SMOKE, 0o755)
    data = build_run(root / "datadir" / "run1", suite, **run_kw)
    submitted = {"job_id": "4242", "data_dir": str(data), "system": "nplb"}
    put(world["crabdir"] / "run.out", json.dumps(submitted, indent=2) + "\n")
    put(world["crabdir"] / "states", states)
    pre = root / "smoke_preflight.sh"
    put(pre, FAKE_PREFLIGHT_SMOKE, 0o755)
    return {"GPU_PREFLIGHT": str(pre), "SMOKE_POLL": "0"}


def crab_calls(world: dict) -> list[str]:
    calls = world["crabdir"] / "calls"
    return calls.read_text().splitlines() if calls.exists() else []


def order(world: dict) -> list[str]:
    log = world["root"] / "order.log"
    return log.read_text().splitlines() if log.exists() else []


def smoke_log(world: dict) -> str:
    return (runs(world)[0] / "smoke.log").read_text()


def verdict_of(out: str) -> str:
    return next(ln for ln in reversed(out.splitlines()) if ln.startswith("smoke: "))


def table_row(out: str, exp: str) -> list[str]:
    """The cells of the screen row of `exp` (cells are separated by 2+ spaces)."""
    line = next(ln for ln in out.splitlines() if ln.startswith(exp))
    return re.split(r"\s{2,}", line.strip())


def median_fn(table: dict):
    return lambda exp, app, size: table[exp][size]


MEDIANS = {
    "ppn1_alone": {SIZE_SMALL: 2e-4, SIZE_LARGE: 3e-2},
    "ppn1_vs_a2a": {SIZE_SMALL: 4e-4, SIZE_LARGE: 6e-2},
    "ppn8_alone": {SIZE_SMALL: 1.5e-3, SIZE_LARGE: 0.12},
    "ppn8_vs_a2a": {SIZE_SMALL: 3e-3, SIZE_LARGE: 1.2},
}


@pytest.fixture
def smoke_world(tmp_path: Path) -> dict:
    return make_world(tmp_path, ["head", "w1", "w2"])


def test_a_healthy_smoke_prints_the_whole_answer_on_one_screen(smoke_world):
    w = smoke_world
    env = smoke_env(w, median=median_fn(MEDIANS))
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    lines = out.splitlines()
    screen_limits(out)
    assert lines[0].startswith("gpu_lab smoke")
    assert re.search(r"^preset\s+OK\s+\S*nplb_h100_gpu\.json", out, re.M)
    assert re.search(r"^validate smoke\s+OK\s+\S*smoke_gpu_a2a_comm\.json$", out, re.M), out
    # did it run: job id, final state, wall time (from the worker log), apps done / expected
    assert re.search(r"^job\s+OK\s+4242 COMPLETED\s+run 1m35s", out, re.M), out
    assert re.search(r"^apps\s+OK\s+8/8 victim apps finished, 40 rows$", out, re.M), out
    # what did the victim measure: one row per experiment, one column per size, units chosen
    header = next(ln for ln in lines if ln.startswith("experiment"))
    assert re.split(r"\s{2,}", header.strip())[:4] == ["experiment", "apps", "1MiB", "64MiB"]
    expected = {
        "ppn1_alone": ["200 us", "30 ms"],
        "ppn1_vs_a2a": ["400 us", "60 ms"],
        "ppn8_alone": ["1.5 ms", "120 ms"],
        "ppn8_vs_a2a": ["3 ms", "1.2 s"],
    }
    for exp, cells in expected.items():
        row = table_row(out, exp)
        assert row[1] == "2/2"
        assert row[2:4] == cells
        assert row[4:] == ["5,5", "12s,12s"], row
    # did the NICs see anything: the three lines of the diff, with the proxy caveat
    for line in DIFF_LINES:
        assert line in lines
    # what will the full suites cost
    assert re.search(
        r"^full suites: ~\d+ \+ ~\d+ min \(rough: smoke ran 25 iterations per app, "
        r"full runs 150\.\.1100\)$",
        out,
        re.M,
    ), out
    assert lines[-1] == "smoke: 0 FAIL, 0 WARN"
    assert [ln for ln in lines if " WARN " in ln or " FAIL " in ln] == []
    assert (runs(w)[0] / "smoke.log").is_file()
    shown = run_lab(w, ["show", "smoke"])
    assert shown.returncode == 0 and shown.stdout == smoke_log(w)


def test_the_log_keeps_the_timestamps_the_derivations_and_the_detail(smoke_world):
    w = smoke_world
    result = run_lab(w, ["smoke", "w1", "w2"], **smoke_env(w))
    assert result.returncode == 0, result.stdout + result.stderr
    log = smoke_log(w)
    for what in ("submitted", "started", "ended"):
        assert re.search(rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d {what}\b", log, re.M), log
    assert "wall time per app" in log and "Launched" in log and "FINISHED" in log
    assert "job 4242 state PENDING" in log and "job 4242 state COMPLETED" in log
    assert "port_xmit_data" in log
    for exp in SMOKE_EXPS:
        assert f"{exp}/data_app_0.csv" in log
    assert "full suites model" in log


def test_the_submit_is_exactly_crab_run_with_the_preset_from_the_checkout(smoke_world):
    w = smoke_world
    result = run_lab(w, ["smoke", "w1"], **smoke_env(w))
    assert result.returncode == 0, result.stdout
    runs_ = [c for c in crab_calls(w) if c.startswith("run ")]
    assert runs_ == [f"run -p nplb_h100_gpu --json {SMOKE_JSON}"]
    assert Path((w["crabdir"] / "run.pwd").read_text().strip()).resolve() == REPO.resolve()
    status = [c for c in crab_calls(w) if c.startswith("status")]
    assert status == ["status --json 4242"] * 3


def test_a_smoke_json_override_is_submitted_and_judged_by_its_own_apps(smoke_world):
    w = smoke_world
    suite = load_smoke_suite(AG_SMOKE_JSON)
    env = smoke_env(w, suite=suite)
    result = run_lab(w, ["smoke", "w1"], SMOKE_JSON=str(AG_SMOKE_JSON), **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"run -p nplb_h100_gpu --json {AG_SMOKE_JSON}" in crab_calls(w)
    assert f"validate -p nplb_h100_gpu {AG_SMOKE_JSON}" in crab_calls(w)
    assert re.search(r"^validate smoke\s+OK\s+\S*smoke_gpu_ag_comm\.json$", result.stdout, re.M)
    assert "8/8 victim apps finished" in result.stdout


def test_a_relative_smoke_json_is_made_absolute_before_the_submit(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    rel = os.path.relpath(SMOKE_JSON, Path.cwd())
    result = run_lab(w, ["smoke", "w1"], SMOKE_JSON=rel, **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"run -p nplb_h100_gpu --json {SMOKE_JSON}" in crab_calls(w)


# ---- the gates before the submit -------------------------------------------------------------


def test_a_missing_preset_fails_and_nothing_is_submitted(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    shutil.rmtree(w["presets"])
    w["presets"].mkdir()
    result = run_lab(w, ["smoke", "w1"], **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^preset\s+FAIL\s+no \*\.json in ", out, re.M), out
    assert SMOKE_POINTER in out
    assert verdict_of(out) == "smoke: 1 FAIL, 0 WARN"
    assert not any(c.startswith(("run", "status")) for c in crab_calls(w))
    assert order(w) == []
    assert "submitted" not in smoke_log(w)


def test_a_failing_validate_shows_its_first_line_and_nothing_is_submitted(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    put(w["crabdir"] / "validate.out", "\ncrab: bad config: no apps\nsecond line\n")
    put(w["crabdir"] / "validate.rc", "1\n")
    result = run_lab(w, ["smoke", "w1"], **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^validate smoke\s+FAIL\s+crab: bad config: no apps$", out, re.M), out
    assert "second line" not in out and "second line" in smoke_log(w)
    assert SMOKE_POINTER in out
    assert crab_calls(w) == [f"validate -p nplb_h100_gpu {SMOKE_JSON}"]
    assert order(w) == [f"crab validate -p nplb_h100_gpu {SMOKE_JSON}"]


def test_a_missing_smoke_file_or_crab_fails_before_anything_runs(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    result = run_lab(w, ["smoke", "w1"], SMOKE_JSON=str(w["root"] / "nope.json"), **env)
    assert result.returncode == 1
    assert re.search(r"^smoke file\s+FAIL\s+.*nope\.json", result.stdout, re.M), result.stdout
    result = run_lab(w, ["smoke", "w1"], CRAB=str(w["root"] / "nocrab"), **env)
    assert result.returncode == 1
    assert re.search(r"^crab\s+FAIL\s+.*nocrab", result.stdout, re.M), result.stdout
    assert order(w) == []


def test_the_full_preflight_is_not_part_of_smoke(smoke_world):
    w = smoke_world
    result = run_lab(w, ["smoke", "w1", "w2"], **smoke_env(w))
    assert result.returncode == 0
    assert all(" counters " in f" {c} " for c in order(w) if c.startswith("preflight"))
    assert "node " not in result.stdout


# ---- counters --------------------------------------------------------------------------------


def test_counters_are_snapshotted_before_the_submit_and_diffed_after_the_job(smoke_world):
    w = smoke_world
    result = run_lab(w, ["smoke", "w2", "head"], **smoke_env(w))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = order(w)
    counters = f"{runs(w)[0]}/smoke.counters"
    snap = f"preflight counters snapshot {counters} w2 head"
    diff = f"preflight counters diff {counters} w2 head"
    run = f"crab run -p nplb_h100_gpu --json {SMOKE_JSON}"
    assert [c for c in calls if "validate" not in c and "status" not in c] == [snap, run, diff]
    last_status = max(i for i, c in enumerate(calls) if c.startswith("crab status"))
    assert calls.index(snap) < calls.index(run) < calls.index(diff) and last_status < calls.index(
        diff
    )
    for line in DIFF_LINES:
        assert line in result.stdout.splitlines()
    assert "w1   mlx5_0   port_xmit_data           +4096" in smoke_log(w)


def test_without_hosts_the_counters_use_the_nodes_of_the_partition(smoke_world):
    w = smoke_world
    result = run_lab(w, ["smoke"], PARTITION="p", **smoke_env(w))
    assert result.returncode == 0, result.stdout + result.stderr
    snaps = [c for c in order(w) if "counters snapshot" in c]
    assert len(snaps) == 1 and snaps[0].endswith("smoke.counters head w1 w2 w3")
    assert "-p p" in (w["root"] / "ssh.log.sinfo").read_text()


def test_a_failing_snapshot_is_a_warning_the_smoke_still_counts_and_no_diff_runs(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    put(w["crabdir"] / "snap.rc", "1\n")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert re.search(r"^counters\s+WARN\s+snapshot failed: w2 ssh FAIL", out, re.M), out
    assert verdict_of(out) == "smoke: 0 FAIL, 1 WARN"
    assert re.search(r"^job\s+OK\s+4242 COMPLETED", out, re.M)
    assert not any("counters diff" in c for c in order(w))


@pytest.mark.parametrize(
    "diff_out, diff_err, detail",
    [
        ("", "gpu_preflight: cannot read /x/smoke.counters\n", "gpu_preflight: cannot read"),
        (
            "ports with traffic: w1/mlx5_0\nECN/CNP activity: unknown\nproxy: p\n"
            "unreachable, not in this diff: w2\n",
            "",
            "unreachable, not in this diff: w2",
        ),
    ],
)
def test_a_failing_diff_is_a_warning_with_its_reason_and_never_a_crash(
    smoke_world, diff_out, diff_err, detail
):
    w = smoke_world
    env = smoke_env(w)
    put(w["crabdir"] / "diff.out", diff_out)
    put(w["crabdir"] / "diff.err", diff_err)
    put(w["crabdir"] / "diff.rc", "1\n")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(rf"^counters\s+WARN\s+.*{re.escape(detail)}", result.stdout, re.M)
    assert verdict_of(result.stdout) == "smoke: 0 FAIL, 1 WARN"
    assert "Traceback" not in result.stdout + result.stderr


def test_no_nodes_to_read_counters_from_is_a_warning_and_the_smoke_goes_on(smoke_world):
    w = smoke_world
    put(w["gbin"] / "sinfo", "#!/usr/bin/env bash\nexit 0\n", 0o755)
    result = run_lab(w, ["smoke"], **smoke_env(w))
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^counters\s+WARN\s+no nodes", result.stdout, re.M), result.stdout
    assert not any("counters" in c for c in order(w))
    assert re.search(r"^job\s+OK", result.stdout, re.M)


# ---- failure paths ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "CANCELLED by 1000", "OUT_OF_MEMORY"]
)
def test_a_job_that_ends_badly_is_one_fail_with_the_pointer(smoke_world, state):
    w = smoke_world
    env = smoke_env(w, states=f"PENDING\nRUNNING\n{state}\n")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1, result.stdout
    out = result.stdout
    screen_limits(out)
    assert re.search(rf"^job\s+FAIL\s+4242 {re.escape(state)}\b", out, re.M), out
    assert SMOKE_POINTER in out
    assert verdict_of(out) == "smoke: 1 FAIL, 0 WARN"
    # the job ended: its counters are still read
    assert any("counters diff" in c for c in order(w))


def test_a_failed_submit_is_a_fail_with_the_last_stderr_line(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    put(w["crabdir"] / "run.rc", "2\n")
    put(w["crabdir"] / "run.err", "INFO loading preset\ncrab: no preset selected\n")
    result = run_lab(w, ["smoke", "w1"], **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^submit\s+FAIL\s+crab run exited 2: crab: no preset selected$", out, re.M)
    assert SMOKE_POINTER in out
    assert verdict_of(out) == "smoke: 1 FAIL, 0 WARN"
    assert not any(c.startswith("status") for c in crab_calls(w))
    assert not any("counters diff" in c for c in order(w))
    assert "INFO loading preset" in smoke_log(w)


@pytest.mark.parametrize("text", ["this is not json\n", "{}\n", '{"job_id": "7"}\n', ""])
def test_an_unparsable_submit_result_is_a_fail(smoke_world, text):
    w = smoke_world
    env = smoke_env(w)
    put(w["crabdir"] / "run.out", text)
    result = run_lab(w, ["smoke", "w1"], **env)
    assert result.returncode == 1
    assert re.search(r"^submit\s+FAIL\s+crab run printed no job_id", result.stdout, re.M)
    assert not any(c.startswith("status") for c in crab_calls(w))


def test_an_app_without_a_csv_or_with_no_rows_names_the_experiment_and_the_app(smoke_world):
    w = smoke_world
    env = smoke_env(w, skip_csv={("ppn1_vs_a2a", "1")}, empty={("ppn8_alone", "0")})
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^app ppn1_vs_a2a/1\s+FAIL\s+no data CSV", out, re.M), out
    assert re.search(r"^app ppn8_alone/0\s+FAIL\s+CSV has 0 rows", out, re.M), out
    assert re.search(r"^apps\s+FAIL\s+6/8 victim apps finished", out, re.M), out
    assert SMOKE_POINTER in out
    lines = out.splitlines()
    # the pointer sits under the failures, above the table
    assert next(i for i, ln in enumerate(lines) if SMOKE_POINTER in ln) < next(
        i for i, ln in enumerate(lines) if ln.startswith("experiment")
    )
    assert verdict_of(out) == "smoke: 3 FAIL, 0 WARN"
    assert table_row(out, "ppn1_vs_a2a")[1] == "1/2"
    assert table_row(out, "ppn1_vs_a2a")[4:][0] == "5,-"


def test_an_app_that_never_finished_or_exited_non_zero_is_a_fail(smoke_world):
    w = smoke_world
    env = smoke_env(w, no_finish={("ppn1_alone", "1")}, failed={("ppn8_vs_a2a", "0")})
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^app ppn1_alone/1\s+FAIL\s+launched, never finished", out, re.M), out
    assert re.search(r"^app ppn8_vs_a2a/0\s+FAIL\s+exited 1", out, re.M), out
    assert re.search(r"^apps\s+FAIL\s+6/8 victim apps finished", out, re.M), out


def test_an_experiment_folder_that_is_missing_counts_its_apps_as_not_done(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    shutil.rmtree(w["root"] / "datadir" / "run1" / "ppn8_alone")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1
    assert re.search(r"^ppn8_alone\s+FAIL\s+no folder in the data dir", result.stdout, re.M)
    assert re.search(r"^apps\s+FAIL\s+6/8 victim apps finished", result.stdout, re.M)


def test_a_wait_that_times_out_leaves_the_job_running_and_says_how_to_look_or_cancel(
    smoke_world,
):
    w = smoke_world
    env = smoke_env(w, states="PENDING\nRUNNING\n")
    result = run_lab(w, ["smoke", "w1", "w2"], SMOKE_TIMEOUT="0", **env)
    assert result.returncode == 1
    out = result.stdout
    assert re.search(r"^job\s+FAIL\s+4242 still PENDING after 0s: not cancelled", out, re.M), out
    assert "crab status 4242" in out and "crab cancel 4242" in out
    assert SMOKE_POINTER in out
    assert verdict_of(out) == "smoke: 1 FAIL, 0 WARN"
    # one poll, no cancel of any kind, no diff of a job that did not end
    assert [c for c in crab_calls(w) if c.startswith("status")] == ["status --json 4242"]
    assert not any("cancel" in c for c in crab_calls(w))
    assert not any("counters diff" in c for c in order(w))


def test_a_status_that_never_answers_ends_as_a_fail_after_a_bounded_number_of_polls(smoke_world):
    w = smoke_world
    env = smoke_env(w, states="NOSTATUS\n")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1
    assert re.search(r"^job\s+FAIL\s+4242 no state from crab status", result.stdout, re.M)
    polls = [c for c in crab_calls(w) if c.startswith("status")]
    assert 2 <= len(polls) <= 100
    assert not any("cancel" in c for c in crab_calls(w))


def test_the_first_error_line_is_on_the_screen_truncated_and_in_the_log_whole(smoke_world):
    w = smoke_world
    long_line = "UCX ERROR no device found " + "x" * 200
    errors = {("ppn8_alone", "1"): long_line + "\nsecond error line\n"}
    env = smoke_env(w, failed={("ppn8_alone", "1")}, errors=errors)
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 1
    out = result.stdout
    screen_limits(out)
    line = next(ln for ln in out.splitlines() if ln.startswith("first error:"))
    assert "ppn8_alone/error_app_1.log" in line and "UCX ERROR no device found" in line
    assert line.endswith("~") and len(line) == SCREEN_COLS
    log = smoke_log(w)
    assert long_line in log and "second error line" in log


def test_slurm_error_log_is_the_fallback_error_source_and_is_quiet_on_a_good_run(smoke_world):
    w = smoke_world
    env = smoke_env(w, states="RUNNING\nFAILED\n", slurm_error="\nTraceback: boom\n")
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert re.search(r"^first error: slurm_error\.log: Traceback: boom$", result.stdout, re.M)
    # a good run with a noisy stderr shows nothing of it
    (w["root"] / "second").mkdir()
    w2 = make_world(w["root"] / "second", ["head", "w1", "w2"])
    env = smoke_env(w2, slurm_error="warning: something harmless\n")
    result = run_lab(w2, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0
    assert "harmless" not in result.stdout and "harmless" in smoke_log(w2)


# ---- the estimate ----------------------------------------------------------------------------


def test_the_estimate_of_a_flat_world_is_hand_computable(smoke_world):
    # every iteration takes 1 ms at both sizes and an app costs 12 s of startup. Per experiment
    # of the full files: 8 sizes x 1100 + 3 sizes x 150 = 9250 iterations = 9.25 s, 11 victim
    # apps x 12 s = 132 s; 8 experiments: 8 x 141.25 s = 1130 s = 18.83 min.
    w = smoke_world
    result = run_lab(w, ["smoke", "w1", "w2"], **smoke_env(w, median=1e-3, wall=12))
    assert result.returncode == 0, result.stdout
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("full suites:"))
    assert line == (
        "full suites: ~19 + ~19 min (rough: smoke ran 25 iterations per app, full runs 150..1100)"
    )
    log = smoke_log(w)
    assert "startup 12 s" in log
    assert "a2a and allgather share one time model" in log


def test_the_estimate_interpolates_between_the_smoke_sizes_and_never_goes_below_them(
    smoke_world,
):
    # 1 ms at 1 MiB and 64 ms at 64 MiB: time proportional to the size (slope 1 in log-log), so
    # 4 MiB 4 ms, 16 MiB 16 ms, 256 MiB 256 ms, 512 MiB 512 ms; below 1 MiB the 1 ms floor holds.
    # ms per experiment: 1100 x (1+1+1+1+1+1+4+16) + 150 x (64+256+512) = 153400 = 153.4 s,
    # plus 11 x 12 s = 132 s: 285.4 s; 8 experiments: 2283.2 s = 38.05 min.
    w = smoke_world
    env = smoke_env(w, median=lambda exp, app, size: 1e-3 if size == SIZE_SMALL else 64e-3)
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("full suites:"))
    assert line.startswith("full suites: ~38 + ~38 min (rough: smoke ran 25 iterations")


def test_an_estimate_without_any_csv_row_says_so(smoke_world):
    w = smoke_world
    every = {(e, a) for e in SMOKE_EXPS for a in ("0", "1")}
    result = run_lab(w, ["smoke", "w1", "w2"], **smoke_env(w, empty=every))
    assert result.returncode == 1
    assert "full suites: no estimate (no data)" in result.stdout.splitlines()


def test_wall_times_come_from_the_log_and_a_missing_one_is_a_question_mark_not_an_error(
    smoke_world,
):
    w = smoke_world
    env = smoke_env(w, no_log={("ppn1_alone", "0"), ("ppn1_alone", "1")}, wall=30)
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0, result.stdout
    out = result.stdout
    assert table_row(out, "ppn1_alone")[1] == "2/2"
    assert table_row(out, "ppn1_alone")[-1] == "?,?"
    assert table_row(out, "ppn1_vs_a2a")[-1] == "30s,30s"
    assert verdict_of(out) == "smoke: 0 FAIL, 0 WARN"
    assert "no Launched/FINISHED lines" in smoke_log(w)


def test_a_wall_time_across_midnight_stays_positive(smoke_world):
    w = smoke_world
    env = smoke_env(w, start=24 * 3600 - 20, wall=12)
    result = run_lab(w, ["smoke", "w1", "w2"], **env)
    assert result.returncode == 0, result.stdout
    for exp in SMOKE_EXPS:
        assert table_row(result.stdout, exp)[-1] == "12s,12s"


# ---- the screen limit ------------------------------------------------------------------------


def wide_suite(count: int) -> dict:
    suite = load_smoke_suite()
    base = suite["experiments"]["ppn1_alone"]
    suite["experiments"] = {
        f"experiment_with_a_long_name_{i:02d}": copy.deepcopy(base) for i in range(count)
    }
    return suite


def test_eight_experiments_fit_on_the_screen_with_the_verdict_last(smoke_world):
    w = smoke_world
    suite = wide_suite(8)
    path = w["root"] / "eight.json"
    path.write_text(json.dumps(suite))
    result = run_lab(w, ["smoke", "w1"], SMOKE_JSON=str(path), **smoke_env(w, suite=suite))
    assert result.returncode == 0, result.stdout + result.stderr
    screen_limits(result.stdout)
    assert result.stdout.splitlines()[-1] == "smoke: 0 FAIL, 0 WARN"
    assert "more lines" not in result.stdout


def test_a_stress_run_is_cut_to_the_screen_says_how_much_and_keeps_the_verdict(smoke_world):
    w = smoke_world
    suite = wide_suite(16)
    path = w["root"] / "sixteen.json"
    path.write_text(json.dumps(suite))
    every = {(e, a) for e in suite["experiments"] for a in ("0", "1")}
    errors = {(e, "0"): f"ERR {e} " + "e" * 250 + "\n" for e in suite["experiments"]}
    env = smoke_env(w, suite=suite, skip_csv=every, errors=errors, slurm_error="x" * 300)
    result = run_lab(w, ["smoke", "w1"], SMOKE_JSON=str(path), **env)
    assert result.returncode == 1
    out = result.stdout
    lines = out.splitlines()
    screen_limits(out)
    assert len(lines) == SCREEN_LINES
    m = re.search(r"^\.\.\. (\d+) more lines, see: gpu_lab\.sh show smoke$", out, re.M)
    assert m and int(m.group(1)) >= 1
    assert re.fullmatch(r"smoke: \d+ FAIL, 0 WARN", lines[-1]), lines[-1]
    assert "~" in out
    log = smoke_log(w)
    assert "ERR experiment_with_a_long_name_15 " + "e" * 250 in log
    for e in suite["experiments"]:
        assert f"app {e}/0" in log and f"app {e}/1" in log


# ---- progress line ---------------------------------------------------------------------------


def test_a_progress_line_shows_only_on_a_terminal(smoke_world):
    import pty

    w = smoke_world
    env = smoke_env(w, states="PENDING\nRUNNING\nCOMPLETED\n")
    plain = run_lab(w, ["smoke", "w1"], **env)
    assert "waiting" not in plain.stdout and "\r" not in plain.stdout
    # a second world so the run folders and the call counters start clean
    (w["root"] / "tty").mkdir()
    w2 = make_world(w["root"] / "tty", ["head", "w1", "w2"])
    env2 = smoke_env(w2, states="PENDING\nRUNNING\nCOMPLETED\n")
    full_env = {**os.environ, "PATH": f"{w2['gbin']}:{w2['base']}", "PYTHON": sys.executable}
    full_env.update(
        {
            "FAKE_HOSTS_DIR": str(w2["root"] / "hosts"),
            "FAKE_SSH_LOG": str(w2["root"] / "ssh.log"),
            "FAKE_CRAB_DIR": str(w2["crabdir"]),
            "FAKE_ORDER": str(w2["root"] / "order.log"),
            "CRAB": str(w2["gbin"] / "crab"),
            "CRAB_PRESETS_DIR": str(w2["presets"]),
            "WORK": str(w2["work"]),
            **env2,
        }
    )
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), "smoke", "w1"],
        env=full_env,
        stdout=slave,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    os.close(slave)
    chunks = []
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    proc.wait(timeout=60)
    os.close(master)
    text = b"".join(chunks).decode(errors="replace")
    assert proc.returncode == 0, text
    assert "\rwaiting: job 4242 " in text


# ---- usage -----------------------------------------------------------------------------------


def test_the_usage_lists_smoke_and_its_settings(world):
    result = run_lab(world, ["-h"])
    assert "gpu_lab.sh smoke [HOST...]" in result.stdout
    assert "SMOKE_JSON" in result.stdout and "SMOKE_TIMEOUT" in result.stdout
    assert "SMOKE_POLL" in result.stdout and "PYTHON" in result.stdout
    assert "smoke, " not in result.stdout.split("Steps still to come")[1].split(":")[1]


def test_bad_smoke_settings_exit_2_with_one_line(smoke_world):
    w = smoke_world
    env = smoke_env(w)
    for name in ("SMOKE_POLL", "SMOKE_TIMEOUT"):
        result = run_lab(w, ["smoke", "w1"], **{**env, name: "soon"})
        assert result.returncode == 2
        assert result.stdout == ""
        assert len(result.stderr.strip().splitlines()) == 1 and name in result.stderr
    assert order(w) == []
