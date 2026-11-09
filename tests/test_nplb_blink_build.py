"""examples/nplb/blink_build.sh (`nplb build blink|check`), run with bash against a fake lab.

Everything lives under tmp_path: a lab folder (ompi-cuda, ucx-cuda, a real tiny git checkout of
Blink cloned from a bare origin), stub cmake/ssh/ldd/rm on PATH, and a PATH that holds nothing
else but symlinks to the few real tools the script needs. No network, no real ssh, cmake or MPI.

The pager cuts a line at 100 columns and pytest paths are long, so details are asserted on the
report the pager saves whole, and only the verdict on the printed page."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SCRIPT = NPLB / "blink_build.sh"
BASH = shutil.which("bash") or "/bin/bash"
NOW = "2026-11-09T14:07:00"
BINARIES = ["alltoall_comm_only", "allgather_comm_only", "allreduce_b"]
FIX_MESSAGE = "widen int*int buffer offsets to size_t"
TOOLS = (
    "bash sh git awk sed sort grep head tail cat date mkdir mv dirname basename uname python3 "
    "find tr cut wc ls stat env readlink mktemp touch chmod ln tee cp id sleep"
).split()
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.org",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.org",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}

STUB_CMAKE = r"""#!/usr/bin/env bash
if [[ ${1:-} == --version ]]; then
    echo "cmake version 3.28.1"
    exit 0
fi
echo "PATH $PATH" >>"$FAKE_CMAKE_LOG"
echo "ARGS $*" >>"$FAKE_CMAKE_LOG"
if [[ $1 == --build ]]; then
    if [[ ${FAKE_CMAKE_FAIL:-} == build ]]; then
        echo "[ 50%] Building CXX object x.o"
        echo "src/x.cpp:3:5: error: boom"
        exit 1
    fi
    mkdir -p "$2/bin"
    for b in alltoall_comm_only allgather_comm_only allreduce_b; do
        printf '#!/bin/sh\n# usage: -blength -bburst\n' >"$2/bin/$b"
        chmod +x "$2/bin/$b"
    done
    echo "[100%] Built target"
    exit 0
fi
if [[ ${FAKE_CMAKE_FAIL:-} == configure ]]; then
    echo "CMake Error: could not find MPI"
    exit 1
fi
while (($#)); do
    [[ $1 == -B ]] && mkdir -p "$2"
    shift
done
echo "-- configured"
"""
STUB_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1
shift
echo "$host $*" >>"$FAKE_SSH_LOG"
export FAKE_HOST=$host
exec bash -c "$*"
"""
STUB_LDD = r"""#!/usr/bin/env bash
if [[ -n ${FAKE_LDD_MISSING_HOST:-} && ${FAKE_LDD_MISSING_HOST} == "${FAKE_HOST:-}" ]]; then
    printf '\tlibucp.so.0 => not found\n'
fi
printf '\tlinux-vdso.so.1 (0x00007ffd)\n'
printf '\tlibmpi.so.40 => %s/libmpi.so.40 (0x00007f01)\n' "${FAKE_LIBMPI_DIR:-$FAKE_LAB/ompi-cuda/lib}"
printf '\tlibcudart.so.12 => /cuda/libcudart.so.12 (0x00007f02)\n'
"""
STUB_RM = '#!/usr/bin/env bash\necho "rm $*" >>"$FAKE_RM_LOG"\nexit 1\n'
STUB_SINFO = (
    '#!/usr/bin/env bash\necho "$*" >>"$FAKE_SSH_LOG.sinfo"\nprintf "n3\\nn1\\nn4\\nn2\\nn1\\n"\n'
)
STUB_UCX_INFO = (
    "#!/usr/bin/env bash\n"
    'if [[ $1 == -v ]]; then\n    printf "# Library version: 1.20.0\\n# API headers version: 1.20.0\\n"\nfi\n'
)
STUB_MPIRUN = '#!/usr/bin/env bash\necho "mpirun (Open MPI) 4.1.6"\necho "Report bugs to x"\n'


def make_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )
    return result.stdout.strip()


def commit(seed: Path, name: str, message: str) -> None:
    (seed / name).write_text(message + "\n")
    git(seed, "add", name)
    git(seed, "commit", "-q", "-m", message)
    git(seed, "push", "-q", str(seed.parent / "origin.git"), "HEAD:main")


def make_site(tmp_path: Path, *, size_t_fix: bool = True, with_blink: bool = True) -> dict:
    lab = tmp_path / "lab"
    lab.mkdir()
    git(lab, "init", "-q", "-b", "main")
    git(lab, "commit", "-q", "--allow-empty", "-m", "lab commit")
    make_exe(lab / "ompi-cuda" / "bin" / "mpirun", STUB_MPIRUN)
    make_exe(lab / "ompi-cuda" / "bin" / "mpicc", "#!/usr/bin/env bash\nexit 0\n")
    (lab / "ompi-cuda" / "lib").mkdir()
    (lab / "ompi-cuda" / "lib" / "libmpi.so.40").touch()
    make_exe(lab / "ucx-cuda" / "bin" / "ucx_info", STUB_UCX_INFO)
    (tmp_path / "cuda").mkdir()

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "commit", "-q", "--allow-empty", "-m", "first commit")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(tmp_path / "origin.git"))
    commit(seed, "a.txt", FIX_MESSAGE if size_t_fix else "tidy the readme")
    if with_blink:
        git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(lab / "blink-gpu"))
        bindir = lab / "blink-gpu" / "build-cuda" / "bin"
        for name in BINARIES:
            make_exe(bindir / name, "#!/bin/sh\n# usage: -blength -bburst\n")

    bin_dir = tmp_path / "bin"
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        real = shutil.which(name)
        if real:
            (tools / name).symlink_to(real)
    make_exe(bin_dir / "cmake", STUB_CMAKE)
    make_exe(bin_dir / "ssh", STUB_SSH)
    make_exe(bin_dir / "ldd", STUB_LDD)
    make_exe(bin_dir / "rm", STUB_RM)
    (tmp_path / "home").mkdir()
    return {
        "tmp": tmp_path,
        "lab": lab,
        "src": lab / "blink-gpu",
        "seed": seed,
        "origin": tmp_path / "origin.git",
        "cuda": tmp_path / "cuda",
        "bin": bin_dir,
        "tools": tools,
        "reports": tmp_path / "reports",
        "ssh_log": tmp_path / "ssh.log",
        "cmake_log": tmp_path / "cmake.log",
        "rm_log": tmp_path / "rm.log",
    }


@pytest.fixture
def site(tmp_path: Path) -> dict:
    return make_site(tmp_path)


def env_for(site: dict, **extra: str) -> dict:
    env = {
        "PATH": f"{site['bin']}:{site['tools']}",
        "HOME": str(site["tmp"] / "home"),
        "NPLB_REPORT_DIR": str(site["reports"]),
        "NPLB_NOW": NOW,
        "NPLB_LAB": str(site["lab"]),
        "CUDA_ROOT": str(site["cuda"]),
        "BLINK_REPO": str(site["origin"]),
        "FAKE_LAB": str(site["lab"]),
        "FAKE_SSH_LOG": str(site["ssh_log"]),
        "FAKE_CMAKE_LOG": str(site["cmake_log"]),
        "FAKE_RM_LOG": str(site["rm_log"]),
        "PYTHONPATH": "/bad/pythonpath",
        **GIT_ENV,
    }
    env.update(extra)
    return env


def run(site: dict, *args: str, script: Path = SCRIPT, **env: str) -> subprocess.CompletedProcess:
    cmd = [BASH, str(script), *args]
    return subprocess.run(
        cmd,
        env=env_for(site, **env),
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def saved(site: dict, name: str, reports: Path | None = None) -> list[str]:
    """The whole report the pager saved under NAME (newest one)."""
    found = sorted((reports or site["reports"]).glob(f"*_{name}*.txt"), key=os.path.getmtime)
    assert found, f"no saved report {name}"
    return found[-1].read_text().splitlines()


def content(stdout: str) -> list[str]:
    return stdout.splitlines()[1:-1]


def line_starting(lines: list[str], prefix: str) -> str:
    matches = [ln for ln in lines if ln.startswith(prefix)]
    assert matches, f"no line starting {prefix!r} in:\n" + "\n".join(lines)
    return matches[0]


def tree_snapshot(root: Path) -> list[tuple]:
    entries = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        entries.append((str(path.relative_to(root)), st.st_size, st.st_mtime_ns))
    return entries


def file_names(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ---- build check ----


def test_build_check_all_ok(site):
    result = run(site, "check", "n1", "n2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("build-check |")
    assert content(result.stdout)[0] == "VERDICT: OK"
    lines = saved(site, "build-check")
    lab = site["lab"]
    assert line_starting(lines, "OK ompi-cuda:").endswith("mpirun (Open MPI) 4.1.6")
    assert "1.20.0" in line_starting(lines, "OK ucx-cuda:")
    assert str(site["cuda"]) in line_starting(lines, "OK cuda root:")
    source = line_starting(lines, "OK blink source:")
    assert "size_t offset fix: yes" in source
    for name in BINARIES:
        assert line_starting(lines, f"OK {name}:")
    for host in ("n1", "n2"):
        assert f"libs: ok (libmpi from {lab}/ompi-cuda)" in line_starting(lines, f"OK {host} libs:")
    assert line_starting(lines, "OK lab clone:").endswith("lab commit")
    assert not any(ln.startswith(("FAIL", "WARN")) for ln in lines)
    assert len(lines) <= 30
    # the ldd runs with the lab's libraries in the path, once per host, through ssh
    calls = site["ssh_log"].read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["n1", "n2"]
    want = f"{lab}/ucx-cuda/lib:{lab}/ompi-cuda/lib:{site['cuda']}/targets/x86_64-linux/lib"
    assert f"LD_LIBRARY_PATH='{want}'" in calls[0]


def test_build_check_fails_when_a_library_is_missing(site):
    result = run(site, "check", "n1", "n2", FAKE_LDD_MISSING_HOST="n2")
    assert result.returncode == 1
    assert content(result.stdout)[0].startswith("VERDICT: FAIL")
    lines = saved(site, "build-check")
    bad = line_starting(lines, "FAIL n2 libs:")
    assert "libucp.so.0" in bad and "not found" in bad
    assert line_starting(lines, "OK n1 libs:")


def test_build_check_fails_without_the_size_t_fix(tmp_path):
    site = make_site(tmp_path, size_t_fix=False)
    result = run(site, "check", "n1")
    assert result.returncode == 1
    assert content(result.stdout)[0].startswith("VERDICT: FAIL")
    bad = line_starting(saved(site, "build-check"), "FAIL blink source:")
    assert "no size_t offset fix" in bad


def test_build_check_flags_a_binary_without_burst_flags(site):
    make_exe(site["src"] / "build-cuda" / "bin" / "allreduce_b", "#!/bin/sh\n# no flags\n")
    result = run(site, "check", "n1")
    assert result.returncode == 1
    lines = saved(site, "build-check")
    assert "-blength" in line_starting(lines, "FAIL allreduce_b:")
    assert line_starting(lines, "OK alltoall_comm_only:")


def test_build_check_missing_lab_dir(site):
    result = run(site, "check", "n1", NPLB_LAB=str(site["tmp"] / "nowhere"))
    assert result.returncode == 1
    assert content(result.stdout)[0].startswith("VERDICT: FAIL")
    lines = saved(site, "build-check")
    for item in ("ompi-cuda", "ucx-cuda", "blink source", *BINARIES):
        assert line_starting(lines, f"FAIL {item}:")
    assert line_starting(lines, "WARN n1 libs:")
    assert line_starting(lines, "WARN lab clone:")
    everything = result.stdout + result.stderr + "\n".join(lines)
    assert "unbound variable" not in everything
    assert "Traceback" not in everything
    assert not site["ssh_log"].exists()


def test_build_check_is_read_only(site):
    before = tree_snapshot(site["lab"])
    result = run(site, "check", "n1", "n2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert tree_snapshot(site["lab"]) == before
    calls = site["ssh_log"].read_text().splitlines()
    assert len(calls) == 2
    for call in calls:
        assert re.fullmatch(r"\S+ LD_LIBRARY_PATH='[^']*' ldd '[^']*'", call), call
    assert not site["rm_log"].exists()
    assert not site["cmake_log"].exists()


# ---- build blink ----


def test_build_blink_moves_the_old_build_dir_aside(site):
    old = site["src"] / "build-cuda"
    (old / "stale.txt").write_text("old cache\n")
    before = file_names(site["lab"])
    result = run(site, "build", "n1")
    assert result.returncode == 0, result.stdout + result.stderr
    archived = list((site["lab"] / "archive").glob("build-cuda-*"))
    assert len(archived) == 1
    assert (archived[0] / "stale.txt").read_text() == "old cache\n"
    assert not (old / "stale.txt").exists()
    assert (old / "bin" / "alltoall_comm_only").is_file()
    # nothing was deleted: every file of the lab before is still there (moved or not)
    after = file_names(site["lab"])
    new_home = f"archive/{archived[0].name}/"
    old_home = "blink-gpu/build-cuda/"
    gone = {
        name
        for name in before
        if name not in after and name.replace(old_home, new_home, 1) not in after
    }
    assert not gone, gone
    assert not site["rm_log"].exists()
    assert str(archived[0]) in "\n".join(saved(site, "build-blink"))


def test_build_blink_pulls_main_configures_and_builds(site):
    commit(site["seed"], "b.txt", "a newer change on main")
    newest = git(site["seed"], "rev-parse", "HEAD")
    assert git(site["src"], "rev-parse", "HEAD") != newest
    result = run(site, "build", "n1", JOBS="8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(site["src"], "rev-parse", "HEAD") == newest
    calls = [ln for ln in site["cmake_log"].read_text().splitlines() if ln.startswith("ARGS ")]
    assert len(calls) == 2
    configure, build = calls
    for flag in (
        "-DBLINK_GPU=CUDA",
        f"-DCUDAToolkit_ROOT={site['cuda']}",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-S {site['src']} -B {site['src']}/build-cuda",
    ):
        assert flag in configure, configure
    assert build.startswith(f"ARGS --build {site['src']}/build-cuda")
    assert "-j8" in build.split()


def test_build_blink_uses_the_cuda_mpi_first(site):
    result = run(site, "build", "n1")
    assert result.returncode == 0, result.stdout + result.stderr
    paths = [ln for ln in site["cmake_log"].read_text().splitlines() if ln.startswith("PATH ")]
    assert paths
    for entry in paths:
        assert entry.split(" ", 1)[1].split(":")[0] == f"{site['lab']}/ompi-cuda/bin"


def test_build_blink_report_proves_the_build(site):
    result = run(site, "build", "n1", "n2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("build-blink |")
    assert content(result.stdout)[0] == "VERDICT: OK"
    lines = saved(site, "build-blink")
    head = git(site["src"], "log", "-1", "--format=%h %ad %s", "--date=short")
    source = line_starting(lines, "OK blink source:")
    assert head in source
    assert "size_t offset fix: yes" in source
    for name in BINARIES:
        assert line_starting(lines, f"OK {name}:")
    for host in ("n1", "n2"):
        want = f"libs: ok (libmpi from {site['lab']}/ompi-cuda)"
        assert want in line_starting(lines, f"OK {host} libs:")
    assert any(ln.startswith("log: ") and "blink_build_" in ln for ln in lines)
    assert list((site["lab"] / "out").glob("blink_build_*.log"))


@pytest.mark.parametrize("stage", ["build", "configure"])
def test_build_blink_fails_loudly_when_the_build_fails(site, stage):
    (site["src"] / "build-cuda" / "stale.txt").write_text("old\n")
    result = run(site, "build", "n1", FAKE_CMAKE_FAIL=stage)
    assert result.returncode == 1
    assert content(result.stdout)[0] == f"VERDICT: FAIL {stage}"
    lines = saved(site, "build-blink")
    text = "\n".join(lines)
    assert ("error: boom" if stage == "build" else "CMake Error") in text
    log = next(ln for ln in lines if ln.startswith("full log: "))
    assert Path(log.split(": ", 1)[1]).is_file()
    archived = list((site["lab"] / "archive").glob("build-cuda-*"))
    assert len(archived) == 1 and (archived[0] / "stale.txt").is_file()
    assert str(archived[0]) in text
    assert not site["ssh_log"].exists()


def test_build_blink_narrates_live_on_stderr(site):
    result = run(site, "build", "n1")
    assert result.returncode == 0, result.stdout + result.stderr
    err = result.stderr.splitlines()
    assert err and all(re.match(r"\[\d\d:\d\d:\d\d\] ", ln) for ln in err), result.stderr
    order = [
        next(i for i, ln in enumerate(err) if re.search(pattern, ln))
        for pattern in (
            r"\] update source",
            r"\] move old build",
            r"\] configure",
            r"\] build ",
            r"\] check libs",
        )
    ]
    assert order == sorted(order)
    out = result.stdout.splitlines()
    assert out[0].startswith("build-blink |")
    assert not any(re.match(r"\[\d\d:\d\d:\d\d\]", ln) for ln in out)
    assert "Built target" not in result.stdout


def test_build_blink_clones_when_the_checkout_is_absent(tmp_path):
    site = make_site(tmp_path, with_blink=False)
    result = run(site, "build", "n1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(site["src"], "log", "-1", "--format=%s") == FIX_MESSAGE
    assert (site["src"] / "build-cuda" / "bin" / "allreduce_b").is_file()


def test_build_blink_pull_failure_is_a_warning(site):
    shutil.move(str(site["origin"]), str(site["tmp"] / "origin-gone.git"))
    result = run(site, "build", "n1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert content(result.stdout)[0].startswith("VERDICT: OK, WARN 1")
    assert "git pull failed" in line_starting(saved(site, "build-blink"), "WARN source update:")


def test_build_blink_refuses_without_the_cuda_mpi_and_keeps_the_old_build(site):
    (site["lab"] / "ompi-cuda" / "bin" / "mpicc").unlink()
    (site["src"] / "build-cuda" / "stale.txt").write_text("old\n")
    result = run(site, "build", "n1")
    assert result.returncode == 2
    assert "mpicc" in result.stderr
    assert (site["src"] / "build-cuda" / "stale.txt").is_file()
    assert not (site["lab"] / "archive").exists()
    assert not site["cmake_log"].exists()


# ---- hosts ----


def test_hosts_default_and_arguments(site):
    run(site, "check", "n3", "n1")
    assert [c.split()[0] for c in site["ssh_log"].read_text().splitlines()] == ["n3", "n1"]
    assert line_starting(saved(site, "build-check"), "hosts:") == "hosts: n3 n1"

    # no HOST and no sinfo: this node only, checked without ssh
    quiet = site["tmp"] / "reports-local"
    site["ssh_log"].unlink()
    result = run(site, "check", NPLB_REPORT_DIR=str(quiet))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = saved(site, "build-check", quiet)
    assert "hosts: this node only (no HOST given, no sinfo)" in lines
    assert line_starting(lines, "OK localhost libs:")
    assert not site["ssh_log"].exists()

    # sinfo lists the nodes of the partition
    make_exe(site["bin"] / "sinfo", STUB_SINFO)
    listed = site["tmp"] / "reports-sinfo"
    result = run(site, "check", NPLB_REPORT_DIR=str(listed))
    assert result.returncode == 0, result.stdout + result.stderr
    hosts = [c.split()[0] for c in site["ssh_log"].read_text().splitlines()]
    assert hosts == ["n1", "n2", "n3", "n4"]
    asked = Path(str(site["ssh_log"]) + ".sinfo").read_text()
    assert "-p h100_4nodes" in asked
    assert any(ln.startswith("hosts: n1 n2 n3 n4") for ln in saved(site, "build-check", listed))


# ---- the dispatcher ----


@pytest.fixture
def checkout(site) -> Path:
    chk = site["tmp"] / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for rel in ("nplb", "blink_build.sh", "pages.py", "lib/pages.sh"):
        shutil.copy(NPLB / rel, nplb_dir / rel)
    return chk


def test_dispatcher_build_verbs(site, checkout):
    nplb = checkout / "examples" / "nplb" / "nplb"
    result = run(site, "build", "check", "n1", script=nplb)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("build-check |")
    assert content(result.stdout)[0] == "VERDICT: OK"
    assert [c.split()[0] for c in site["ssh_log"].read_text().splitlines()] == ["n1"]

    result = run(site, "build", "blink", "n1", script=nplb)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("build-blink |")

    for args in ((), ("frob",)):
        bad = run(site, "build", *args, script=nplb)
        assert bad.returncode == 2
        assert "usage: nplb build blink|check [HOST...]" in bad.stderr

    helped = run(site, "help", script=nplb)
    assert helped.returncode == 0
    lines = helped.stdout.splitlines()
    assert any(ln.startswith("  build blink") for ln in lines)
    assert any(ln.startswith("  build check") for ln in lines)
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
