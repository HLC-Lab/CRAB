"""`crab probe` warns on a live node when it probably describes a login node or sees other GPUs than sysfs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from crab.cli import probe
from crab.core.execution.probe_sysfs import collect_node
from sysfs_fixtures import four_by_four

LOGIN = (
    "no Slurm task seen on n1 (SLURMD_NODENAME is not set to this host): on a login node this "
    "describes the login node, not a compute node; run it inside an allocation, for example with srun"
)


def live(environ: dict[str, str], visible: int | None = None, n: int = 4) -> list[str]:
    return probe._live_warnings("n1", environ, n, "nvidia", visible)


def test_an_unset_slurmd_nodename_gives_the_login_node_warning() -> None:
    assert live({}) == [LOGIN]


def test_a_slurmd_nodename_naming_this_host_gives_no_login_warning() -> None:
    assert live({"SLURMD_NODENAME": "n1"}) == []
    assert probe._live_warnings("n1", {"SLURMD_NODENAME": "n1.cluster"}, 4, "nvidia", None) == []
    assert probe._live_warnings("n1.cluster", {"SLURMD_NODENAME": "n1"}, 4, "nvidia", None) == []


def test_a_slurmd_nodename_naming_another_host_gives_the_warning() -> None:
    assert live({"SLURMD_NODENAME": "n2"}) == [LOGIN]


def test_fewer_visible_gpus_than_sysfs_lists_warn_with_the_cause() -> None:
    assert live({"SLURMD_NODENAME": "n1"}, visible=2) == [
        "2 of the 4 GPUs sysfs lists are visible to this process (a Slurm job sees only the GPUs "
        "it was given): probe on a whole-node allocation to get every GPU"
    ]


def test_more_visible_gpus_than_sysfs_lists_warn_that_the_block_is_missing_some() -> None:
    assert live({"SLURMD_NODENAME": "n1"}, visible=6) == [
        "nvidia-smi sees 6 GPUs but sysfs listed 4: the block is missing some"
    ]


def test_no_gpu_warning_when_equal_unknown_or_not_nvidia() -> None:
    environ = {"SLURMD_NODENAME": "n1"}
    assert live(environ, visible=4) == []
    assert live(environ, visible=None) == []
    assert probe._live_warnings("n1", environ, 4, "amd", 1) == []
    assert probe._live_warnings("n1", environ, 0, "none", 1) == []


def run_live(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    visible: int | None,
    **options: Any,
) -> str:
    """Run the command as on a live node and return its stdout (`_read_node` returns a fixture node).

    The real signal is that `probe.handle_probe(args)` returns without raising `SystemExit`: an
    exit would propagate and fail the calling test.
    """
    node = collect_node(four_by_four(tmp_path / "sys").root, hostname="n1")
    monkeypatch.setattr(probe, "_read_node", lambda args: node)
    monkeypatch.setattr(probe, "visible_gpu_count", lambda: visible)
    monkeypatch.delenv("SLURMD_NODENAME", raising=False)
    defaults = {"sysfs": "/sys", "node_file": None, "json": True}
    defaults |= {"check": False, "preset": None, "topology_file": None}
    args = argparse.Namespace(**{**defaults, **options})
    probe.handle_probe(args)
    return capsys.readouterr().out


def test_a_live_probe_prints_both_warnings_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = run_live(monkeypatch, capsys, tmp_path, visible=2)
    warnings = json.loads(out)["warnings"]
    assert LOGIN in warnings
    assert any(w.startswith("2 of the 4 GPUs sysfs lists are visible") for w in warnings)
    human = run_live(monkeypatch, capsys, tmp_path / "again", visible=2, json=False)
    assert f"warning: {LOGIN}" in human


def test_a_probe_of_a_sysfs_copy_or_a_saved_node_adds_neither_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = run_live(monkeypatch, capsys, tmp_path, visible=2, sysfs=str(tmp_path / "sys"))
    assert json.loads(out)["warnings"] == []
    out = run_live(
        monkeypatch, capsys, tmp_path / "again", visible=2, node_file=str(tmp_path / "n.json")
    )
    assert json.loads(out)["warnings"] == []


def test_an_unreadable_node_file_exits_1_and_names_the_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "missing.json"
    defaults = {"sysfs": "/sys", "json": True, "check": False, "preset": None}
    args = argparse.Namespace(**defaults, node_file=str(missing), topology_file=None)
    with pytest.raises(SystemExit) as exit_info:
        probe.handle_probe(args)
    assert exit_info.value.code == 1
    assert capsys.readouterr().err.startswith(f"crab: cannot read {missing}: ")
