"""examples/nplb/gpu_lab.sh (skeleton, `status`, `show`), run with bash against fakes in tmp_path.

The fakes are a per-host world for the real gpu_preflight.sh (fake ssh, sinfo, nvidia-smi, ldd,
ompi_info, findmnt, mlxreg and a fake sysfs), a fake `crab` that answers `validate` with chosen
text and exit code, and, for the screen-limit cases, a fake GPU_PREFLIGHT script. Nothing real runs
and nothing sends a signal; the script's WORK folder is a tmp_path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
    }
    for name in ("PARTITION", "CRAB_PATH_PRESETS", "GPU_PREFLIGHT"):
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


@pytest.mark.parametrize("step", ["smoke", "dual-port", "pin", "all"])
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
