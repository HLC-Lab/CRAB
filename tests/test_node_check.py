"""Checking a node against a preset's topology, and nodes against each other (ADR-043).

Before: a placement trusted the topology and nothing looked at the node, so a port that had gone down
or a job that held two of four GPUs only showed up as odd results. Trees are synthetic (`sysfs_fixtures`).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest

from crab.cli import probe
from crab.core.execution import node_check
from crab.core.execution.node_check import (
    Used,
    allowed_cores,
    check_node,
    compare_nodes,
    count_gpu_lines,
    node_facts,
    notes_for,
    visible_gpu_count,
)
from crab.core.execution.probe_sysfs import collect_node
from crab.core.execution.probe_topology import build_topology
from crab.core.execution.topology import Topology, parse_topology
from sysfs_fixtures import Sysfs, four_by_four

EVERYTHING = Used(frozenset({f"mlx5_{k}:1" for k in range(4)}), True, frozenset({0, 1, 8, 9}))


def topology_of(fs: Sysfs) -> Topology:
    block, _ = build_topology(collect_node(fs.root, hostname="n"), "2026-11-01")
    return parse_topology(block, "preset 'p'")


def node_of(fs: Sysfs) -> dict[str, Any]:
    return collect_node(fs.root, hostname="n")


def test_a_node_matches_the_topology_the_probe_wrote_for_it(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    allowed = frozenset(range(16))
    assert check_node(node_of(fs), topology_of(fs), EVERYTHING, 4, allowed) == []


def test_a_port_that_went_down_is_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    (tmp_path / "class" / "infiniband" / "mlx5_2" / "ports" / "1" / "state").write_text("1: DOWN\n")
    assert check_node(node_of(fs), topology, EVERYTHING) == ["mlx5_2:1 is not active (1: DOWN)"]


def test_a_port_the_node_does_not_have_is_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    shutil.rmtree(tmp_path / "class" / "infiniband" / "mlx5_3")
    assert check_node(node_of(fs), topology, EVERYTHING) == ["mlx5_3:1 is not on this node"]


def test_a_nic_in_another_pci_slot_than_the_topology_says_is_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    moved = tuple(
        dataclasses.replace(nic, pci="0000:ff:00.0") if nic.dev == "mlx5_1:1" else nic
        for nic in topology.nics
    )
    problems = check_node(node_of(fs), dataclasses.replace(topology, nics=moved), EVERYTHING)
    assert problems == ["mlx5_1:1 is at PCI 0000:21:00.0, the topology says 0000:ff:00.0"]


def test_a_missing_gpu_and_a_gpu_in_another_slot_are_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    swapped = tuple(
        dataclasses.replace(gpu, pci="0000:aa:00.0") if index == 2 else gpu
        for index, gpu in enumerate(topology.gpus)
    )
    problems = check_node(node_of(fs), dataclasses.replace(topology, gpus=swapped), EVERYTHING)
    assert problems == ["GPU 2 is at PCI 0000:12:00.0, the topology says 0000:aa:00.0"]
    (tmp_path / "bus" / "pci" / "devices" / "0000:13:00.0").unlink()
    problems = check_node(node_of(fs), topology, EVERYTHING)
    assert problems == ["the node has 3 GPUs, the topology lists 4"]


def test_a_job_that_sees_fewer_gpus_than_the_node_says_how_to_fix_it(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    problems = check_node(node_of(fs), topology_of(fs), EVERYTHING, visible_gpus=2)
    assert problems == [
        "2 GPUs are visible to the job, the topology lists 4; bind.gpu needs the job to see all "
        "of them (for example --gres=gpu:4)"
    ]


def test_cores_the_job_may_not_use_are_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    used = dataclasses.replace(EVERYTHING, cores=frozenset({0, 1, 9, 10}))
    problems = check_node(node_of(fs), topology_of(fs), used, allowed=frozenset(range(10)))
    assert problems == ["cores 10 are not allowed to the job"]


def test_a_different_numa_layout_is_named(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    other = dataclasses.replace(topology, numa={0: (0, 1, 2, 3), 1: (4, 5, 6, 7)})
    assert check_node(node_of(fs), other, EVERYTHING) == [
        "the node's NUMA domains differ from the topology's"
    ]


def test_only_what_the_placement_uses_is_checked(tmp_path: Path) -> None:
    fs = four_by_four(tmp_path)
    topology = topology_of(fs)
    (tmp_path / "class" / "infiniband" / "mlx5_2" / "ports" / "1" / "state").write_text("1: DOWN\n")
    (tmp_path / "bus" / "pci" / "devices" / "0000:13:00.0").unlink()
    cores_only = Used(frozenset({"mlx5_0:1"}), False, frozenset({0}))
    assert check_node(node_of(fs), topology, cores_only, visible_gpus=1) == []


def test_a_nic_a_literal_config_names_that_the_topology_lacks_is_still_checked(
    tmp_path: Path,
) -> None:
    fs = four_by_four(tmp_path)
    used = Used(frozenset({"mlx5_0:1", "mlx5_9:1"}))
    assert check_node(node_of(fs), topology_of(fs), used) == ["mlx5_9:1 is not on this node"]


def test_count_gpu_lines_counts_only_gpu_rows() -> None:
    listing = "GPU 0: NVIDIA A (UUID: GPU-1)\nGPU 1: NVIDIA A (UUID: GPU-2)\nnote: GPU 9: x\n\n"
    assert count_gpu_lines(listing) == 2
    assert count_gpu_lines("") == 0


def test_visible_gpus_come_from_nvidia_smi_and_none_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "nvidia-smi"
    script.write_text("#!/bin/sh\necho 'GPU 0: A'\necho 'GPU 1: A'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(node_check.shutil, "which", lambda name: str(script))
    assert visible_gpu_count() == 2
    script.write_text("#!/bin/sh\nexit 9\n")
    assert visible_gpu_count() is None
    monkeypatch.setattr(node_check.shutil, "which", lambda name: None)
    assert visible_gpu_count() is None


def test_allowed_cores_read_the_status_file(tmp_path: Path) -> None:
    status = tmp_path / "self"
    status.mkdir()
    (status / "status").write_text("Name:\tx\nCpus_allowed_list:\t0-3,8\nMems_allowed_list:\t0\n")
    assert allowed_cores(tmp_path) == {0, 1, 2, 3, 8}
    (status / "status").write_text("Name:\tx\n")
    assert allowed_cores(tmp_path) is None
    assert allowed_cores(tmp_path / "missing") is None


def facts_of(fs: Sysfs) -> dict[str, Any]:
    return node_facts(node_of(fs))


def test_alike_nodes_have_no_differences(tmp_path: Path) -> None:
    first, second = four_by_four(tmp_path / "a"), four_by_four(tmp_path / "b")
    assert compare_nodes({"n1": facts_of(first), "n2": facts_of(second)}) == []
    assert compare_nodes({"only": facts_of(first)}) == []


def test_a_node_that_lacks_a_port_or_a_gpu_or_has_another_layout_is_named(tmp_path: Path) -> None:
    base = facts_of(four_by_four(tmp_path / "a"))
    other = four_by_four(tmp_path / "b")
    shutil.rmtree(tmp_path / "b" / "class" / "infiniband" / "mlx5_1")
    (tmp_path / "b" / "bus" / "pci" / "devices" / "0000:13:00.0").unlink()
    (tmp_path / "b" / "devices" / "system" / "node" / "node1" / "cpulist").write_text("8-14\n")
    problems = compare_nodes({"n1": base, "n2": facts_of(other)})
    assert problems == [
        "active NIC ports differ from n1: n2 lacks mlx5_1:1",
        "n2 has 3 GPUs, n1 has 4",
        "the NUMA domains of n2 differ from those of n1",
    ]


def test_a_nic_in_another_slot_on_one_node_is_named(tmp_path: Path) -> None:
    base = facts_of(four_by_four(tmp_path / "a"))
    other = facts_of(four_by_four(tmp_path / "b"))
    other["nics"]["mlx5_0:1"] = "0000:99:00.0"
    assert compare_nodes({"n1": base, "n2": other}) == [
        "active NIC ports differ from n1: mlx5_0:1 is in another PCI slot on n2"
    ]


def test_main_prints_one_json_line_about_this_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fs = four_by_four(tmp_path / "sys")
    topology_block, _ = build_topology(node_of(fs), "2026-11-01")
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {"topology": topology_block, "nics": ["mlx5_0:1"], "gpus": True, "cores": [0, 1]}
        )
    )
    monkeypatch.setattr(node_check, "collect_node", lambda path: node_of(fs))
    monkeypatch.setattr(node_check, "visible_gpu_count", lambda: 3)
    monkeypatch.setattr(node_check, "allowed_cores", lambda: frozenset({0, 1}))
    assert node_check.main([str(spec)]) == 0
    line = json.loads(capsys.readouterr().out)
    assert set(line) == {"host", "problems", "facts"}
    assert line["problems"] == [
        "3 GPUs are visible to the job, the topology lists 4; bind.gpu needs the job to see all "
        "of them (for example --gres=gpu:4)"
    ]
    assert line["facts"]["nics"]["mlx5_0:1"] == "0000:20:00.0"
    assert node_check.main([]) == 2


# --- crab probe --check ---------------------------------------------------------------------------


def run_check(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, **options: Any
) -> tuple[str, str, int]:
    monkeypatch.setattr(probe, "visible_gpu_count", lambda: 4)
    monkeypatch.setattr(probe, "allowed_cores", lambda: None)
    defaults: dict[str, Any] = {
        "sysfs": "/sys",
        "node_file": None,
        "json": False,
        "check": True,
        "preset": None,
        "topology_file": None,
    }
    args = argparse.Namespace(**{**defaults, **options})
    try:
        probe.handle_probe(args)
        code = 0
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    captured = capsys.readouterr()
    return captured.out, captured.err, code


def block_file(tmp_path: Path, fs: Sysfs) -> Path:
    block, _ = build_topology(node_of(fs), "2026-11-01")
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(block))
    return path


def test_check_says_ok_when_the_node_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = four_by_four(tmp_path / "sys")
    out, _, code = run_check(
        capsys, monkeypatch, sysfs=str(fs.root), topology_file=str(block_file(tmp_path, fs))
    )
    assert code == 0 and out == "ok: this node matches the topology (4 NIC ports, 4 GPUs)\n"


def test_check_lists_each_problem_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = four_by_four(tmp_path / "sys")
    path = block_file(tmp_path, fs)
    (fs.root / "class" / "infiniband" / "mlx5_1" / "ports" / "1" / "state").write_text("1: DOWN\n")
    out, _, code = run_check(capsys, monkeypatch, sysfs=str(fs.root), topology_file=str(path))
    assert code == 1 and out == "problem: mlx5_1:1 is not active (1: DOWN)\n"
    out, _, code = run_check(
        capsys, monkeypatch, sysfs=str(fs.root), topology_file=str(path), json=True
    )
    assert json.loads(out) == {"ok": False, "problems": ["mlx5_1:1 is not active (1: DOWN)"]}
    assert code == 1


def test_check_accepts_a_probe_json_document_and_a_saved_node(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = four_by_four(tmp_path / "sys")
    node = node_of(fs)
    block, warnings = build_topology(node, "2026-11-01")
    document = tmp_path / "doc.json"
    document.write_text(json.dumps({"node": node, "topology": block, "warnings": warnings}))
    out, _, code = run_check(
        capsys, monkeypatch, node_file=str(document), topology_file=str(document)
    )
    assert code == 0 and out.startswith("ok:")


def test_check_against_a_preset_uses_its_topology(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = four_by_four(tmp_path / "sys")
    block, _ = build_topology(node_of(fs), "2026-11-01")
    monkeypatch.setattr("crab.paths.crab_home", lambda: str(tmp_path))
    monkeypatch.setattr(
        "crab.cli.presets.load_all_presets",
        lambda root: {
            "has": {"scheduler": "slurm", "topology": block},
            "bare": {"scheduler": "slurm"},
        },
    )
    out, _, code = run_check(capsys, monkeypatch, sysfs=str(fs.root), preset="has")
    assert code == 0 and out.startswith("ok:")
    _, err, code = run_check(capsys, monkeypatch, sysfs=str(fs.root), preset="bare")
    assert (
        code == 1
        and err == "crab: preset 'bare' has no topology (crab probe prints one for a node)\n"
    )
    _, err, code = run_check(capsys, monkeypatch, sysfs=str(fs.root), preset="nope")
    assert code == 1 and err.startswith("crab: no preset 'nope'; try one of: bare, has")


def test_check_without_something_to_check_against_or_with_unreadable_input_is_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, err, code = run_check(capsys, monkeypatch)
    assert (
        code == 1
        and err == "crab: --check needs a preset (-p) or a topology file (--topology-file)\n"
    )
    _, err, code = run_check(capsys, monkeypatch, topology_file=str(tmp_path / "missing.json"))
    assert code == 1 and err.startswith("crab: cannot read ")
    fs = four_by_four(tmp_path / "sys")
    _, err, code = run_check(
        capsys,
        monkeypatch,
        sysfs=str(tmp_path / "nope"),
        topology_file=str(block_file(tmp_path, fs)),
    )
    assert code == 1 and err.startswith("crab: no sysfs at ")
    assert os.path.exists(tmp_path)


def test_the_check_options_are_registered() -> None:
    parser = argparse.ArgumentParser()
    probe.register(parser.add_subparsers())
    args = parser.parse_args(["probe", "--check", "-p", "lab", "--topology-file", "t.json"])
    assert (args.check, args.preset, args.topology_file) == (True, "lab", "t.json")


def test_a_node_that_cannot_check_prints_its_crash_instead_of_staying_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert node_check.main([str(tmp_path / "no-such-spec.json")]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["facts"] is None and len(line["problems"]) == 1
    assert line["problems"][0].startswith("the node check crashed: FileNotFoundError: ")


def test_a_note_says_the_visible_gpus_could_not_be_counted_only_when_gpus_are_used() -> None:
    note = ["visible GPUs not verified: nvidia-smi gave no count"]
    assert notes_for(Used(gpus=True), None) == note
    assert notes_for(Used(gpus=True), 4) == []
    assert notes_for(Used(gpus=True), 3) == []
    assert notes_for(Used(gpus=False), None) == []
    assert notes_for(Used(nics=frozenset({"mlx5_0:1"}), cores=frozenset({0, 1})), None) == []


def test_main_adds_notes_to_its_line_only_when_the_gpu_count_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fs = four_by_four(tmp_path / "sys")
    topology_block, _ = build_topology(node_of(fs), "2026-11-01")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"topology": topology_block, "gpus": True}))
    monkeypatch.setattr(node_check, "collect_node", lambda path: node_of(fs))
    monkeypatch.setattr(node_check, "allowed_cores", lambda: None)
    monkeypatch.setattr(node_check, "visible_gpu_count", lambda: None)
    assert node_check.main([str(spec)]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["problems"] == []
    assert line["notes"] == ["visible GPUs not verified: nvidia-smi gave no count"]
    monkeypatch.setattr(node_check, "visible_gpu_count", lambda: 4)
    assert node_check.main([str(spec)]) == 0
    assert "notes" not in json.loads(capsys.readouterr().out)
    spec.write_text(json.dumps({"topology": topology_block, "gpus": False}))
    monkeypatch.setattr(node_check, "visible_gpu_count", lambda: None)
    assert node_check.main([str(spec)]) == 0
    assert "notes" not in json.loads(capsys.readouterr().out)
