"""Phase 1 tests for the CLI `--json` contract seam (crab.cli.contract).

Pure / injectable — no real cluster, no SSH, no Slurm. Subprocess-backed
gatherers use a fake command runner.
"""

from __future__ import annotations

import json
from pathlib import Path

from crab.cli import contract


# --------------------------------------------------------------------------- #
# info
# --------------------------------------------------------------------------- #
def test_gather_info_parses_and_filters_presets(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "presets.json").write_text(
        json.dumps(
            {
                "_common": {"description": "shared"},
                "example_preset": {"description": "skip me"},
                "leonardo": {"description": "Leonardo @ CINECA"},
                "alps": {"description": "ALPS"},
            }
        )
    )
    data = contract.gather_info(crab_root=tmp_path)

    assert data["schema"] == contract.CONTRACT_SCHEMA
    assert isinstance(data["crab_version"], str) and data["crab_version"]
    assert data["crab_root"] == str(tmp_path)
    names = [p["name"] for p in data["presets"]]
    assert names == ["alps", "leonardo"]  # sorted; _common/example excluded


def test_gather_info_missing_presets_is_graceful(tmp_path: Path):
    data = contract.gather_info(crab_root=tmp_path)  # no config/presets.json
    assert data["presets"] == []


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #
def _write_registry(system_dir: Path, rows: list[dict]) -> None:
    system_dir.mkdir(parents=True)
    header = ",".join(contract._HISTORY_COLUMNS)
    lines = [header]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in contract._HISTORY_COLUMNS))
    (system_dir / "metadata.csv").write_text("\n".join(lines) + "\n")


def test_gather_history_scans_all_systems(tmp_path: Path):
    _write_registry(
        tmp_path / "leonardo",
        [
            {
                "job_name": "j1",
                "experiment_name": "e1",
                "timestamp": "2026-01-01_00-00-00",
                "status": "COMPLETED",
                "apps_list": "blink",
                "numnodes": "2",
                "ppn": "4",
            }
        ],
    )
    _write_registry(
        tmp_path / "local",
        [
            {
                "job_name": "j2",
                "experiment_name": "e2",
                "timestamp": "2026-02-02_00-00-00",
                "status": "FAILED",
                "apps_list": "g500",
            }
        ],
    )
    data = contract.gather_history(data_root=tmp_path)
    assert data["schema"] == contract.CONTRACT_SCHEMA
    by_system = {e["system"]: e for e in data["experiments"]}
    assert set(by_system) == {"leonardo", "local"}
    assert by_system["leonardo"]["status"] == "COMPLETED"
    assert by_system["leonardo"]["apps_list"] == "blink"


def test_gather_history_system_filter_and_empty(tmp_path: Path):
    _write_registry(tmp_path / "leonardo", [{"job_name": "j1", "experiment_name": "e1"}])
    only = contract.gather_history(data_root=tmp_path, system="leonardo")
    assert len(only["experiments"]) == 1
    # Unknown system / no data dir → empty, no crash.
    assert contract.gather_history(data_root=tmp_path, system="ghost")["experiments"] == []
    assert contract.gather_history(data_root=tmp_path / "nope")["experiments"] == []


# --------------------------------------------------------------------------- #
# benchmarks + wrappers
# --------------------------------------------------------------------------- #
_GOOD_WRAPPER = """
class app:
    metadata = [
        {"name": "duration", "unit": "s", "conv": True},
        {"name": "bw", "unit": "Gb/s", "conv": False},
    ]
    def __init__(self, id_num, collect_flag, args):
        pass
    @property
    def benchmark_id(self):
        return "demo"
    def get_bench_name(self):
        return "Demo Bench"
"""

_BROKEN_WRAPPER = "import a_module_that_does_not_exist_xyz\n"


def test_gather_benchmarks_receipts_and_wrappers(tmp_path: Path):
    env = tmp_path / "environments"
    env.mkdir()
    (env / "blink.json").write_text(
        json.dumps(
            {
                "id": "blink",
                "type": "source",
                "binary_path": "/x/bin",
                "launcher_override": "",
                "hooks": {"pre_run": ["module load gcc"]},
            }
        )
    )

    wdir = tmp_path / "wrappers"
    (wdir / "demo").mkdir(parents=True)
    (wdir / "demo" / "good.py").write_text(_GOOD_WRAPPER)
    (wdir / "demo" / "broken.py").write_text(_BROKEN_WRAPPER)
    (wdir / "demo" / "__init__.py").write_text("")  # must be skipped

    data = contract.gather_benchmarks(env_dir=env, wrappers_dir=wdir)

    assert data["schema"] == contract.CONTRACT_SCHEMA
    assert len(data["benchmarks"]) == 1
    b = data["benchmarks"][0]
    assert b["id"] == "blink" and b["type"] == "source"
    assert b["target_arch"] is None
    assert b["hooks"]["pre_run"] == ["module load gcc"]

    wrappers = {w["file"]: w for w in data["wrappers"]}
    assert "__init__.py" not in wrappers  # underscore files skipped
    good = wrappers["good.py"]
    assert good["loadable"] is True
    assert good["group"] == "demo"
    assert good["benchmark_id"] == "demo"
    assert good["bench_name"] == "Demo Bench"
    assert [m["name"] for m in good["metadata"]] == ["duration", "bw"]
    assert good["metadata"][0]["conv"] is True

    broken = wrappers["broken.py"]
    assert broken["loadable"] is False
    assert "error" in broken


# --------------------------------------------------------------------------- #
# nodes (fake sinfo)
# --------------------------------------------------------------------------- #
def test_gather_nodes_parses_sinfo():
    def fake_runner(cmd: list[str]) -> str:
        if "%R|%a|%D" in cmd:
            return "boost_usr_prod|up|3456\nlrd_all_serial|up|1\nboost_usr_prod|up|3456\n"
        if "%N" in cmd:
            return "lrdn[0001-0026,0028-0038],gpu01\n"
        raise AssertionError(f"unexpected cmd {cmd}")

    data = contract.gather_nodes(runner=fake_runner)
    assert data["available"] is True
    names = [p["name"] for p in data["partitions"]]
    assert names == ["boost_usr_prod", "lrd_all_serial"]  # deduped
    assert data["partitions"][0]["nodes"] == 3456
    assert data["nodes"] == ["lrdn[0001-0026]", "lrdn[0028-0038]", "gpu01"]


def test_gather_nodes_no_sinfo_is_graceful():
    def fake_runner(cmd: list[str]) -> str:
        raise FileNotFoundError("sinfo")

    data = contract.gather_nodes(runner=fake_runner)
    assert data["available"] is False
    assert data["partitions"] == [] and data["nodes"] == []
    assert "note" in data


# --------------------------------------------------------------------------- #
# status (fake squeue/sacct)
# --------------------------------------------------------------------------- #
def test_gather_status_squeue_then_sacct():
    def fake_runner(cmd: list[str]) -> str:
        if cmd[0] == "squeue":
            # only 123 is still queued/running
            return "123|RUNNING\n"
        if cmd[0] == "sacct":
            jid = cmd[2]
            if jid == "456":
                return "456|COMPLETED|0:0\n456.batch|COMPLETED|0:0\n"
            raise __import__("subprocess").CalledProcessError(1, cmd)
        raise AssertionError(cmd)

    data = contract.gather_status(["123", "456", "789"], runner=fake_runner)
    states = {j["job_id"]: j for j in data["jobs"]}
    assert states["123"]["state"] == "RUNNING" and states["123"]["source"] == "squeue"
    assert states["456"]["state"] == "COMPLETED" and states["456"]["exit_code"] == "0:0"
    assert states["789"]["state"] == "UNKNOWN"
    # order preserved
    assert [j["job_id"] for j in data["jobs"]] == ["123", "456", "789"]


# --------------------------------------------------------------------------- #
# cancel (fake scancel)
# --------------------------------------------------------------------------- #
def test_gather_cancel_success():
    calls = []

    def fake_runner(cmd: list[str]) -> str:
        calls.append(cmd)
        return ""

    data = contract.gather_cancel("123", runner=fake_runner)
    assert calls == [["scancel", "123"]]
    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "job_id": "123",
        "cancelled": True,
        "detail": None,
    }


def test_gather_cancel_already_gone():
    import subprocess

    def fake_runner(cmd: list[str]) -> str:
        raise subprocess.CalledProcessError(1, cmd, output="scancel: error: Invalid job id")

    data = contract.gather_cancel("999", runner=fake_runner)
    assert data["cancelled"] is False
    assert data["job_id"] == "999"
    assert data["detail"]  # a human-readable hint, not parsed by callers


def test_gather_cancel_no_scancel_binary():
    def fake_runner(cmd: list[str]) -> str:
        raise FileNotFoundError("scancel")

    data = contract.gather_cancel("123", runner=fake_runner)
    assert data["cancelled"] is False
    assert "scancel" in data["detail"]


# --------------------------------------------------------------------------- #
# emit
# --------------------------------------------------------------------------- #
def test_emit_json_vs_human(capsys):
    contract.emit({"a": 1}, as_json=True, human=lambda d: print("HUMAN"))
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1}

    contract.emit({"a": 1}, as_json=False, human=lambda d: print("HUMAN"))
    assert "HUMAN" in capsys.readouterr().out
