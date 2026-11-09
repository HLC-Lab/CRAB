"""examples/nplb/env.sh (PYTHONPATH, venv check, the nplb function) and the nplb dispatcher.

Everything runs with bash in a fake checkout under tmp_path: copies of env.sh and nplb, and a
`.venv/bin/python` that is a stub script. No real pip, no network, no cluster."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"

# Answers the pandas import check by FAKE_PANDAS (0 = importable), refuses everything else
# (the Blink receipt query then prints nothing).
STUB_PANDAS = r"""#!/usr/bin/env bash
if [[ "$*" == "-c import pandas" ]]; then
    exit "${FAKE_PANDAS:-0}"
fi
exit 1
"""

# Logs "argv | PYTHONPATH | cwd" per call; `-m pip check` fails when FAKE_CHECK_FAIL is set.
STUB_PIP = r"""#!/usr/bin/env bash
echo "$* | ${PYTHONPATH-unset} | $PWD" >>"$FAKE_LOG"
if [[ "$*" == "-m pip check" && -n "${FAKE_CHECK_FAIL:-}" ]]; then
    echo "foo 1.0 requires bar"
    exit 1
fi
exit 0
"""


def make_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def site(tmp_path: Path) -> dict:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    nplb_dir.mkdir(parents=True)
    shutil.copy(NPLB / "env.sh", nplb_dir / "env.sh")
    shutil.copy(NPLB / "nplb", nplb_dir / "nplb")
    (nplb_dir / "nplb").chmod(0o755)
    empty = tmp_path / "no-mpi"
    empty.mkdir()
    (tmp_path / "elsewhere").mkdir()
    return {
        "chk": chk,
        "env_sh": nplb_dir / "env.sh",
        "nplb": nplb_dir / "nplb",
        "python": chk / ".venv" / "bin" / "python",
        "log": tmp_path / "log",
        "empty": empty,
        "elsewhere": tmp_path / "elsewhere",
    }


def env_for(site: dict, **extra: str) -> dict:
    env = {
        **os.environ,
        "NPLB_MPI_ROOT": str(site["empty"]),
        "FAKE_LOG": str(site["log"]),
    }
    for var in ("PYTHONPATH", "BLINK", "NPLB_HOME", "FAKE_PANDAS", "FAKE_CHECK_FAIL"):
        env.pop(var, None)
    env.update(extra)
    return env


def run_bash(site: dict, script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        env=env_for(site, **env),
        cwd=site["elsewhere"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def source_env(site: dict, script: str, **env: str) -> subprocess.CompletedProcess:
    return run_bash(site, f'. "{site["env_sh"]}"; {script}', **env)


def run_nplb(site: dict, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(site["nplb"]), *args],
        env=env_for(site, **env),
        cwd=site["elsewhere"],
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---- env.sh ----


def test_env_unsets_pythonpath(site):
    make_stub(site["python"], STUB_PANDAS)
    result = source_env(site, 'echo "[${PYTHONPATH-unset}]"', PYTHONPATH="/some/dir")
    assert result.returncode == 0, result.stderr
    assert "[unset]" in result.stdout.splitlines()

    # a site file that exports PYTHONPATH itself is neutralised too
    (site["chk"] / "local").mkdir()
    (site["chk"] / "local" / "nplb_env.sh").write_text("export PYTHONPATH=/x\n")
    result = source_env(site, 'echo "[${PYTHONPATH-unset}]"')
    assert "[unset]" in result.stdout.splitlines()


def test_env_says_when_it_dropped_a_pythonpath(site):
    make_stub(site["python"], STUB_PANDAS)
    result = source_env(site, "true", PYTHONPATH="/some/dir")
    assert "PYTHONPATH: unset (was /some/dir)" in result.stdout.splitlines()
    result = source_env(site, "true")
    assert not any(ln.startswith("PYTHONPATH:") for ln in result.stdout.splitlines())


def test_env_warns_when_pandas_is_missing(site):
    make_stub(site["python"], STUB_PANDAS)
    result = source_env(site, 'echo "rc=$?"', FAKE_PANDAS="1")
    lines = result.stdout.splitlines()
    assert any(ln.startswith("warning:") and "nplb fix-venv" in ln for ln in lines)
    assert "rc=0" in lines

    # no python at all is the same warning
    site["python"].unlink()
    result = source_env(site, 'echo "rc=$?"')
    lines = result.stdout.splitlines()
    assert any(ln.startswith("warning:") and "nplb fix-venv" in ln for ln in lines)
    assert "rc=0" in lines


def test_env_is_quiet_about_the_venv_when_pandas_imports(site):
    make_stub(site["python"], STUB_PANDAS)
    result = source_env(site, "true", FAKE_PANDAS="0")
    assert result.returncode == 0, result.stderr
    assert "fix-venv" not in result.stdout
    # the receipt call still behaves as before
    assert "warning: no Blink directory" in result.stdout


def test_env_defines_the_nplb_function(site):
    make_stub(site["python"], STUB_PANDAS)
    result = source_env(site, 'type -t nplb; echo "H=$NPLB_HOME"; nplb help')
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "function" in lines
    assert f"H={site['env_sh'].parent}" in lines
    assert "fix-venv" in result.stdout


def test_nplb_unknown_verb_exits_2(site):
    result = run_nplb(site, "frobnicate")
    assert result.returncode == 2
    assert "frobnicate" in result.stderr
    assert "nplb help" in result.stderr
    bare = run_nplb(site)
    assert bare.returncode == 0
    assert bare.stdout == run_nplb(site, "help").stdout


# ---- nplb fix-venv ----


def test_fix_venv_runs_pip_without_pythonpath(site):
    make_stub(site["python"], STUB_PIP)
    result = run_nplb(site, "fix-venv", PYTHONPATH="/bad")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = site["log"].read_text().splitlines()
    chk = str(site["chk"].resolve())
    assert calls == [
        f"-m pip install -e . | unset | {chk}",
        f"-m pip check | unset | {chk}",
    ]
    assert result.stdout.splitlines()[-1].startswith("OK")


def test_fix_venv_stops_when_pip_check_fails(site):
    make_stub(site["python"], STUB_PIP)
    result = run_nplb(site, "fix-venv", FAKE_CHECK_FAIL="1")
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "foo 1.0 requires bar" in out
    assert out.splitlines()[-1].startswith("FAIL")


def test_fix_venv_without_a_venv_says_how_to_make_one(site):
    result = run_nplb(site, "fix-venv")
    assert result.returncode == 1
    assert "make" in result.stdout + result.stderr


def test_nplb_help_fits_a_page(site):
    result = run_nplb(site, "help")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
