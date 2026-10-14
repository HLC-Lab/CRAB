"""examples/nplb/diagnose.sh, run with bash in a fake checkout under tmp_path.

salloc, sinfo and mpirun are fakes on PATH (mpirun prints --report-bindings lines that depend on
the flags it got), NIC_CHECK is a fake that prints nic_check-like lines whose numbers depend on the
profile's ppn. Nothing here talks to Slurm or a cluster."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
CORES8 = [0, 1, 2, 3, 40, 41, 42, 43]
NICS8 = [f"mlx5_{i}:1" for i in (0, 2, 4, 8, 10, 12, 14, 16)]
NICS_ODD = [f"mlx5_{i}:1" for i in (1, 3, 5, 9, 11, 13, 15, 17)]
CORES16 = [0, 1, 2, 3, 40, 41, 42, 43, 20, 21, 22, 23, 60, 61, 62, 63]

FAKE_SALLOC = r"""#!/usr/bin/env bash
echo "salloc $*" >>"$FAKE_LOG"
if [[ -n ${FAKE_FAIL_MATCH:-} && "$*" == *"$FAKE_FAIL_MATCH"* ]]; then
    echo "salloc: error: fake allocation failure" >&2
    exit 1
fi
while [[ $# -gt 0 ]]; do
    case $1 in
        -p | -t | -w) shift 2 ;;
        -*) shift ;;
        *) break ;;
    esac
done
exec "$@"
"""
FAKE_SINFO = '#!/usr/bin/env bash\nprintf "%s\\n" n3 n1 n0 n2 n1\n'
# cpu-list:ordered binds rank i to the i-th core, cpu-list gives each rank two cores, cpu-set
# binds nothing
FAKE_MPIRUN = r"""#!/usr/bin/env bash
echo "mpirun $*" >>"$FAKE_LOG"
while [[ $# -gt 0 ]]; do
    case $1 in
        -n) n=$2; shift 2 ;;
        --cpu-list | --cpu-set) cores=$2; shift 2 ;;
        --bind-to) bind=$2; shift 2 ;;
        *) shift ;;
    esac
done
IFS=, read -ra c <<<"$cores"
for ((r = 0; r < n; r++)); do
    if [[ $bind == cpu-list:ordered ]]; then
        echo "[h:1] MCW rank $r bound to socket 0[core ${c[r]}[hwt 0-1]]: [BB/../..]" >&2
    elif [[ $bind == cpu-list ]]; then
        echo "[h:1] MCW rank $r bound to socket 0[core ${c[r]}[hwt 0]], socket 0[core ${c[(r + 1) % n]}[hwt 0]]" >&2
    else
        echo "[h:1] MCW rank $r is not bound (or bound to all available processors)" >&2
    fi
done
"""
FAKE_NIC_CHECK = r"""#!/usr/bin/env bash
echo "nic_check args=$*" >>"$FAKE_LOG"
ppn=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["ppn"])' "$1")
pad() { printf '%*s' 35 ''; }
echo "pairwise_nb, 2 nodes"
printf '%-13s %6s Gb/s per rank   n0: mlx5_0 1.00 GB\n' one-rank 220.7
echo "$(pad)unbound ranks: 2"
printf '%-13s %6s Gb/s per rank   n0: mlx5_0 1.00 GB\n' per-rank "$(python3 -c "print(f'{400/$ppn:.1f}')")"
echo "$(pad)unbound ranks: $((2 * ppn))"
echo "$(pad)bindings:"
printf '%-13s %6s Gb/s per rank   n0: mlx5_0 1.00 GB\n' first-nic "$(python3 -c "print(f'{200/$ppn:.1f}')")"
echo "$(pad)unbound ranks: 7"
"""


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def site(tmp_path: Path) -> dict:
    chk = tmp_path / "chk"
    nplb = chk / "examples" / "nplb"
    nplb.mkdir(parents=True)
    for name in ("diagnose.sh", "env.sh"):
        shutil.copy(NPLB / name, nplb / name)
    # answers env.sh's Blink receipt query with the fake blink dir, runs everything else for real
    write_exec(
        chk / ".venv" / "bin" / "python",
        "#!/usr/bin/env bash\n"
        'if [[ ${1:-} == -c && ${2:-} == *crab.setup.memory* ]]; then echo "$FAKE_BLINK"; exit 0; fi\n'
        f'exec {sys.executable} "$@"\n',
    )
    write_exec(tmp_path / "nic_check.sh", FAKE_NIC_CHECK)
    bindir = tmp_path / "bin"
    for name, text in (("salloc", FAKE_SALLOC), ("sinfo", FAKE_SINFO), ("mpirun", FAKE_MPIRUN)):
        write_exec(bindir / name, text)
    (chk / "profile8.json").write_text(
        json.dumps(
            {
                "nodes": ["n0", "n1"],
                "ppn": 8,
                "nics": NICS8,
                "cpus": ",".join(map(str, CORES8)),
                "extra": "kept",
            }
        )
    )
    nics16 = [n for pair in zip(NICS8, NICS_ODD, strict=True) for n in pair]
    (chk / "profile16.json").write_text(
        json.dumps({"nodes": ["n0", "n1"], "ppn": 16, "nics": nics16, "cpus": CORES16})
    )
    (tmp_path / "mpi").mkdir()
    (tmp_path / "blink").mkdir()
    return {"root": tmp_path, "chk": chk, "bin": bindir, "work": chk / "local" / "nplb-work"}


def diagnose(site: dict, **extra: str) -> subprocess.CompletedProcess:
    root = site["root"]
    env = {
        **os.environ,
        "PATH": f"{site['bin']}:{os.environ['PATH']}",
        "NPLB_MPI_ROOT": str(root / "mpi"),
        "FAKE_LOG": str(root / "log"),
        "NIC_CHECK": str(root / "nic_check.sh"),
        "FAKE_BLINK": str(root / "blink"),
    }
    for var in ("PARTITION", "WORK", "PROFILE_DIR", "FAKE_FAIL_MATCH"):
        env.pop(var, None)
    env.update(extra)
    return subprocess.run(
        ["bash", str(site["chk"] / "examples" / "nplb" / "diagnose.sh")],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )


def fake_log(site: dict, prefix: str) -> list[str]:
    path = site["root"] / "log"
    if not path.exists():
        return []
    return [ln for ln in path.read_text().splitlines() if ln.startswith(prefix)]


def summary_block(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    return lines[lines.index("=== SUMMARY") :]


def test_full_run_prints_the_summary_and_keeps_the_log(site):
    result = diagnose(site)
    assert result.returncode == 0, result.stdout + result.stderr
    block = summary_block(result.stdout)
    assert "A1 cpu-list:ordered   8/8 bound  0:0 1:1 2:2 3:3 4:40 5:41 6:42 7:43" in block
    assert (
        "A2 cpu-list           8/8 bound  0:0+1 1:1+2 2:2+3 3:3+40 4:40+41 5:41+42 6:42+43 7:43+0"
        in block
    )
    assert "A3 cpu-set+core       0/8 bound" in block
    assert "B1 n0-n1 k2   one 220.7  per 200.0  first 100.0  unbound 4" in block
    assert "B2 n0-n1 k4   one 220.7  per 100.0  first 50.0  unbound 8" in block
    assert "B3 n0-n1 p8   one 220.7  per 50.0  first 25.0  unbound 16" in block
    assert "B4 n0-n1 odd  one 220.7  per 50.0  first 25.0  unbound 16" in block
    assert "B5 n0-n2 p8   one 220.7  per 50.0  first 25.0  unbound 16" in block
    logs = sorted(site["work"].glob("diagnose_*.log"))
    sums = sorted(site["work"].glob("diagnose_*_summary.txt"))
    assert len(logs) == 1 and len(sums) == 1
    assert block[-1] == f"log: local/nplb-work/{logs[0].name}"
    assert sums[0].read_text().splitlines() == block
    log = logs[0].read_text()
    assert "+ salloc -N1 -w n0 --exclusive -p h100_4nodes -t 00:05:00 mpirun" in log
    assert "+ salloc -N2 -w n0,n2 --exclusive -p h100_4nodes -t 00:20:00 env" in log
    assert "MCW rank 7 bound to socket 0[core 43[hwt 0-1]]" in log
    mpiruns = fake_log(site, "mpirun")
    hosts = ",".join(["n0"] * 8)
    assert mpiruns[0] == (
        f"mpirun --allow-run-as-root -n 8 -H {hosts} --cpu-list 0,1,2,3,40,41,42,43"
        " --bind-to cpu-list:ordered --report-bindings true"
    )
    assert mpiruns[2].endswith(
        "--cpu-set 0,1,2,3,40,41,42,43 --bind-to core --report-bindings true"
    )


def test_generated_profiles_hold_the_expected_nic_core_pairs(site):
    assert diagnose(site).returncode == 0
    work = site["work"]

    def load(name: str) -> dict:
        return json.loads((work / f"diagnose_profile_{name}.json").read_text())

    for name, count in (("k2", 2), ("k4", 4)):
        prof = load(name)
        assert prof["nics"] == NICS8[:count]
        assert prof["cpus"] == ",".join(map(str, CORES8[:count]))
        assert prof["ppn"] == count and prof["extra"] == "kept"
    odd = load("odd")
    assert odd["nics"] == NICS_ODD
    assert odd["cpus"] == ",".join(str(c) for c in sorted(CORES16)[1::2])
    assert odd["ppn"] == 8
    nic_calls = fake_log(site, "nic_check")
    assert nic_calls[2] == f"nic_check args={site['chk']}/profile8.json {site['root']}/blink"
    assert nic_calls[0].split()[1] == f"args={work}/diagnose_profile_k2.json"


def test_a_failing_step_is_recorded_and_the_rest_still_runs(site):
    result = diagnose(site, FAKE_FAIL_MATCH="diagnose_profile_k4")
    assert result.returncode == 1
    block = summary_block(result.stdout)
    assert "B2 n0-n1 k4   FAILED: salloc: error: fake allocation failure" in block
    assert "B1 n0-n1 k2   one 220.7  per 200.0  first 100.0  unbound 4" in block
    assert "B5 n0-n2 p8   one 220.7  per 50.0  first 25.0  unbound 16" in block
    assert len(fake_log(site, "salloc")) == 8


def test_a_failing_first_step_does_not_stop_the_run(site):
    result = diagnose(site, FAKE_FAIL_MATCH="-N1")
    assert result.returncode == 1
    block = summary_block(result.stdout)
    assert sum("FAILED: salloc: error" in ln for ln in block) == 3
    assert any(ln.startswith("B5 ") and "one 220.7" in ln for ln in block)


def test_missing_profile16_is_a_setup_error(site):
    (site["chk"] / "profile16.json").unlink()
    result = diagnose(site)
    assert result.returncode == 2
    assert "profile16.json" in result.stderr
    assert fake_log(site, "salloc") == []


def test_too_few_nodes_is_a_setup_error(site):
    write_exec(site["bin"] / "sinfo", '#!/usr/bin/env bash\nprintf "%s\\n" n0 n1\n')
    result = diagnose(site)
    assert result.returncode == 2
    assert "at least 3 nodes" in result.stderr


def test_the_summary_fits_one_screen(site):
    result = diagnose(site, FAKE_FAIL_MATCH="-N2")
    block = summary_block(result.stdout)
    assert 0 < len(block) <= 20
    assert all(len(ln) <= 100 for ln in block)
    saved = next(site["work"].glob("diagnose_*_summary.txt")).read_text().splitlines()
    assert saved == block
