"""The committed NPLB GPU comm-only suites (examples/nplb/h100/gpu_a2a_comm.json, gpu_ag_comm.json)
launched through CRAB's real launch code: placement (`place_experiment`), the `nplb_h100_gpu`
preset's launcher and `run_job` with a faked `Popen`. The assertions are what would run on the
cluster: the mpirun line, the exports, the cpu binding, the per-rank NIC shim and the binary.

`~` in an app's `binary` is expanded by the wrapper base (`expand_binary`, in `run_app`) in the
process that runs CRAB, with its `HOME`; the script and the line carry the absolute path.
Nothing here starts a process or sends a signal: `Popen` is a fake that records the argv.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crab.cli.presets import resolve_presets
from crab.core.execution.placement import app_ppn, place_experiment
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher
from crab.core.process import manager as process_manager
from crab.wrappers.base import base

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "nplb"
GPU_PRESET_FILE = EXAMPLES / "presets" / "nplb_h100_gpu.json"
GPU_MPIRUN = "/root/crab/lab/ompi-cuda/bin/mpirun"
PRESET_FLAGS = ["--allow-run-as-root", "--mca", "pml", "ucx", "--mca", "btl", "^openib"]
BIN_DIR = "/root/crab/lab/blink-gpu/build-cuda/bin"
HOME = "/root"

# file -> the binary its victim apps run
SUITES = {
    "gpu_a2a_comm.json": f"{BIN_DIR}/alltoall_comm_only",
    "gpu_ag_comm.json": f"{BIN_DIR}/allgather_comm_only",
}
A2A = "gpu_a2a_comm.json"
PPNS = [1, 2, 4, 8]
# The cores of the ppn-8 table; every smaller ppn takes its first entries.
CORES = {1: "0", 2: "0,1", 4: "0,1,2,3", 8: "0,1,2,3,40,41,42,43"}
APP_ENV = {
    "UCX_MAX_RNDV_RAILS": "1",
    "UCX_IB_ROCE_REACHABILITY_MODE": "all",
    "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
}
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
VICTIM_NODES = ["v1", "v2"]
AGGRESSOR_NODES = ["a1", "a2"]


class _FakePopen:
    calls: list[list[str]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        _FakePopen.calls.append(argv)
        self.argv = argv
        self.pid = 4242
        self.returncode = None
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""


@pytest.fixture(autouse=True)
def _fake_popen(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _FakePopen.calls = []
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    before = set(process_manager._registry)
    yield
    for pid in set(process_manager._registry) - before:
        process_manager._registry.pop(pid, None)


def _settings() -> ExecutionSettings:
    resolved = resolve_presets([], [(GPU_PRESET_FILE, json.loads(GPU_PRESET_FILE.read_text()))])
    body = dict(resolved["nplb_h100_gpu"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100_gpu")


def _experiment(file: str, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads((EXAMPLES / "h100" / file).read_text())
    return config["global_options"], config["experiments"][name]


@dataclass
class Launched:
    """What `run_job` wrote and ran for one app."""

    app: dict[str, Any]
    cpu_list: tuple[int, ...] | None
    script: list[str]
    shim: list[str]
    popen_argv: list[str]
    script_path: Path
    shim_path: Path

    @property
    def line(self) -> str:
        """The launch line: the last line of the app script (no experiment dir, so no tee)."""
        return self.script[-1]


def _launch(
    tmp_path: Path, file: str, experiment: str, key: str, environ: dict[str, str] | None = None
) -> Launched:
    """Place the whole experiment, then run app `key` through `run_job`."""
    global_opts, body = _experiment(file, experiment)
    apps: dict[str, dict[str, Any]] = body["apps"]
    keys = list(apps)
    configs = [apps[k] for k in keys]
    nodes = [
        AGGRESSOR_NODES if c.get("partition") == "aggressor" else VICTIM_NODES for c in configs
    ]
    settings = _settings()
    placed = place_experiment(
        experiment,
        keys,
        configs,
        nodes,
        global_opts["allocation"],
        global_opts,
        settings,
        VICTIM_NODES + AGGRESSOR_NODES,
        global_opts,
    )
    i = keys.index(key)
    cfg = configs[i]
    # The runner builds the app from the wrapper and injects the config keys it does not reserve
    # (here `binary`); `base` is the wrapper's launch logic.
    job = base(int(key), bool(cfg["collect"]), cfg["args"])
    job.binary = cfg["binary"]  # type: ignore[attr-defined]
    job.ppn = app_ppn(cfg, global_opts)  # type: ignore[attr-defined]
    job.set_nodes(nodes[i])
    job.cpu_list = placed.cpu_lists[i]
    job.app_env = placed.envs[i]
    job.shared_cores = placed.shared_cores[i]
    job.co_run = placed.co_run[i]  # type: ignore[attr-defined]
    job.nics = placed.nics[i]  # type: ignore[attr-defined]
    job.run_dir = str(tmp_path / f"run_{experiment}" / f"app_{key}")  # type: ignore[attr-defined]
    os.makedirs(job.run_dir, exist_ok=True)  # type: ignore[attr-defined]

    env = {"PATH": "/p", "HOME": HOME, "LD_LIBRARY_PATH": "/usr/lib"}
    env.update(environ or {})
    spec = resolve_launcher(settings, {}, None)
    with patch.dict(os.environ, env, clear=True):
        process_manager.run_job(job, spec, MagicMock())

    wrappers = Path(job.run_dir) / ".wrappers"  # type: ignore[attr-defined]
    shim_path = wrappers / f"app_{key}_rank.sh"
    return Launched(
        app=cfg,
        cpu_list=job.cpu_list,
        script=(wrappers / f"app_{key}.sh").read_text().splitlines(),
        shim=shim_path.read_text().splitlines() if shim_path.exists() else [],
        popen_argv=_FakePopen.calls[-1],
        script_path=wrappers / f"app_{key}.sh",
        shim_path=shim_path,
    )


def _x_names(line: str) -> list[str]:
    words = shlex.split(line)
    return [words[i + 1] for i, word in enumerate(words) if word == "-x"]


def _shim_nics(shim: list[str]) -> list[str]:
    """The entries of the shim's `nics=(...)` array."""
    line = next(row for row in shim if row.startswith("nics=("))
    return shlex.split(line[len("nics=(") : -1])


@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_the_victim_is_launched_by_the_cuda_aware_mpirun_on_its_binary(
    tmp_path: Path, file: str, ppn: int
) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_alone", "0")
    words = shlex.split(launched.line)
    assert words[0] == GPU_MPIRUN
    assert words[1:3] == ["-n", str(ppn * len(VICTIM_NODES))]
    # Open MPI 4 takes a host name once per slot.
    hosts = ",".join(n for n in VICTIM_NODES for _ in range(ppn))
    assert words[3:5] == ["-H", hosts]
    flags_at = words.index("--allow-run-as-root")
    assert words[flags_at : words.index("bash")] == PRESET_FLAGS
    # The shim runs first, then the binary and the app's args (ending in -gpu).
    bash_at = words.index("bash")
    assert words[bash_at + 1] == str(launched.shim_path)
    assert words[bash_at + 2] == SUITES[file]
    assert words[bash_at + 3 :] == shlex.split(launched.app["args"])
    assert words[-1] == "-gpu"
    assert launched.script[0] == "#!/bin/bash"
    assert launched.popen_argv == ["bash", str(launched.script_path)]


def test_the_binary_is_written_expanded_not_with_a_tilde(tmp_path: Path) -> None:
    launched = _launch(tmp_path, A2A, "ppn4_alone", "0")
    # The config keeps `~`; CRAB expands it (HOME of the process that runs it) before the line
    # is built, so neither the script nor the mpirun line contains a literal `~`.
    assert launched.app["binary"] == "~/crab/lab/blink-gpu/build-cuda/bin/alltoall_comm_only"
    assert "~" not in "\n".join(launched.script)
    assert f"{BIN_DIR}/alltoall_comm_only -msgsize 1024" in launched.line


def test_the_ppn4_a2a_victim_line_is_exactly_this(tmp_path: Path) -> None:
    launched = _launch(tmp_path, A2A, "ppn4_alone", "0")
    assert launched.line == (
        f"{GPU_MPIRUN} -n 8 -H v1,v1,v1,v1,v2,v2,v2,v2 "
        "--cpu-list 0,1,2,3 --bind-to cpu-list:ordered "
        "-x PATH -x LD_LIBRARY_PATH -x UCX_MAX_RNDV_RAILS -x UCX_IB_ROCE_REACHABILITY_MODE "
        "-x CUDA_DEVICE_ORDER --report-bindings "
        "--allow-run-as-root --mca pml ucx --mca btl ^openib "
        f"bash {launched.shim_path} {BIN_DIR}/alltoall_comm_only "
        "-msgsize 1024 -iter 1000 -warmup 100 -grty 1 -gpu"
    )


@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_the_app_variables_are_exported_in_the_script_and_named_with_x(
    tmp_path: Path, file: str, ppn: int
) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_alone", "0")
    assert launched.app["env"] == APP_ENV
    exports = [row for row in launched.script if row.startswith("export ")]
    assert exports == [f"export {name}={value}" for name, value in APP_ENV.items()]
    # The library path and PATH are forwarded because the launching environment sets them.
    assert _x_names(launched.line) == ["PATH", "LD_LIBRARY_PATH", *APP_ENV]
    # The NIC is set per rank by the shim on the node: never exported by the script.
    assert "UCX_NET_DEVICES" not in "\n".join(launched.script)
    # The exports come before the launch line.
    assert launched.script.index(exports[-1]) < len(launched.script) - 1


@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_the_victim_is_bound_to_its_placed_cores(tmp_path: Path, file: str, ppn: int) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_alone", "0")
    cores = CORES[ppn]
    assert launched.app["cpus"] == cores
    assert launched.cpu_list == tuple(int(c) for c in cores.split(","))
    # Open MPI 4: an explicit core list and `cpu-list:ordered`, local rank i on the i-th core.
    assert f"--cpu-list {cores} --bind-to cpu-list:ordered" in launched.line


@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_each_local_rank_gets_its_own_nic_from_the_shim(
    tmp_path: Path, file: str, ppn: int
) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_alone", "0")
    assert launched.app["nics"] == NICS[:ppn]
    shim_nics = _shim_nics(launched.shim)
    assert shim_nics == NICS[:ppn]
    assert len(set(shim_nics)) == ppn
    rank_line = next(row for row in launched.shim if row.startswith("r="))
    assert rank_line.startswith("r=${OMPI_COMM_WORLD_LOCAL_RANK:-")
    # Rank r exports nics[r % len(nics)]; with one NIC per rank that is NICS[r].
    assert 'export UCX_NET_DEVICES="${nics[$((10#$r % ${#nics[@]}))]}"' in launched.shim
    for rank in range(ppn):
        assert shim_nics[rank % len(shim_nics)] == NICS[rank]
    # A rank without a local rank variable fails; it never falls back to the first NIC.
    assert "        exit 1" in launched.shim
    assert launched.shim[-1] == 'exec "$@"'


def test_the_ppn4_a2a_shim_is_exactly_this(tmp_path: Path) -> None:
    launched = _launch(tmp_path, A2A, "ppn4_alone", "0")
    assert launched.shim == [
        "#!/bin/bash",
        "# CRAB: each rank of app 0 uses its own NIC from the app's nics list.",
        "nics=(mlx5_0:1 mlx5_2:1 mlx5_4:1 mlx5_8:1)",
        "r=${OMPI_COMM_WORLD_LOCAL_RANK:-${MPI_LOCALRANKID:-${PMI_LOCAL_RANK:-"
        "${PALS_LOCAL_RANKID:-}}}}",
        "case $r in",
        "    ''|*[!0-9]*)",
        '        echo "crab: no local rank variable (OMPI_COMM_WORLD_LOCAL_RANK, MPI_LOCALRANKID, '
        'PMI_LOCAL_RANK, PALS_LOCAL_RANKID) set; cannot pick a NIC" >&2',
        "        exit 1",
        "        ;;",
        "esac",
        'export UCX_NET_DEVICES="${nics[$((10#$r % ${#nics[@]}))]}"',
        'echo "crab: $(uname -n) local rank $r -> UCX_NET_DEVICES=$UCX_NET_DEVICES" >&2',
        'exec "$@"',
    ]


@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_the_aggressor_runs_alltoall_until_the_end_on_its_own_cores_and_nics(
    tmp_path: Path, file: str, ppn: int
) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_vs_a2a", "11")
    app = launched.app
    assert app["partition"] == "aggressor"
    words = shlex.split(launched.line)
    assert words[0] == GPU_MPIRUN
    # The aggressor always runs the maximum ppn (8), whatever the victim's.
    assert words[1:3] == ["-n", str(8 * len(AGGRESSOR_NODES))]
    assert words[3:5] == ["-H", ",".join(n for n in AGGRESSOR_NODES for _ in range(8))]
    bash_at = words.index("bash")
    assert words[bash_at + 2] == f"{BIN_DIR}/alltoall_comm_only"
    args = words[bash_at + 3 :]
    assert args == ["-msgsize", "131072", "-endl", "-grty", "1", "-gpu"]
    assert "-blength" not in words
    assert f"--cpu-list {CORES[8]} --bind-to cpu-list:ordered" in launched.line
    assert _shim_nics(launched.shim) == NICS
    # The nics list is the only NIC source: no UCX_NET_DEVICES from env, in the script or the -x.
    assert "UCX_NET_DEVICES" not in app["env"]
    assert "UCX_NET_DEVICES" not in "\n".join(launched.script)
    assert "UCX_NET_DEVICES" not in _x_names(launched.line)
    assert "UCX_NET_DEVICES" in "\n".join(launched.shim)


@pytest.mark.parametrize("key", ["3", "10"])
@pytest.mark.parametrize("file", SUITES)
@pytest.mark.parametrize("ppn", PPNS)
def test_a_chained_victim_size_gets_its_nic_shim_binary_and_the_heads_cores(
    tmp_path: Path, file: str, ppn: int, key: str
) -> None:
    launched = _launch(tmp_path, file, f"ppn{ppn}_alone", key)
    app = launched.app
    assert app["start"] == f"s{int(key) - 1}"
    # The chained app names no cpus: it runs on its head's cores.
    assert "cpus" not in app
    assert launched.cpu_list == tuple(int(c) for c in CORES[ppn].split(","))
    words = shlex.split(launched.line)
    assert words[0] == GPU_MPIRUN
    assert f"--cpu-list {CORES[ppn]} --bind-to cpu-list:ordered" in launched.line
    bash_at = words.index("bash")
    assert words[bash_at + 1] == str(launched.shim_path)
    assert launched.shim_path.name == f"app_{key}_rank.sh"
    assert words[bash_at + 2] == SUITES[file]
    assert words[bash_at + 3 :] == shlex.split(app["args"])
    assert words[-1] == "-gpu"
    assert _shim_nics(launched.shim) == NICS[:ppn]
    assert "export UCX_NET_DEVICES=" in "\n".join(launched.shim)
    exports = [row for row in launched.script if row.startswith("export ")]
    assert exports == [f"export {name}={value}" for name, value in APP_ENV.items()]
