"""Guard for the wrappers the NPLB campaign blocks use (examples/nplb/campaign.py).

Two things nothing else checks: every app of every generated block names a wrapper that exists in
`wrappers/` and whose `get_path("<name>")` is the program the app's `binary` names; and the shared
Blink parser (`wrappers/blink/_microbench_common.py`, `microbench.read_data`) parses the three
victim programs' output, including output with MPI/UCX/GPU lines before the CSV table.

The stdout samples are SYNTHETIC: they follow Blink's print code (common.h `write_results`: the
header `Average,Minimum,Maximum,Median,MainRank`, one 5-number row per sample, then
`Ran N iterations. Measured M iterations.`), not a capture. There is no real `allreduce_b` output
on the laptop; a real sample from the lab is the later check. Nothing starts a process except the
planner CLI, which writes only under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from conftest import requires_wrappers_checkout

pytestmark = requires_wrappers_checkout

REPO = Path(__file__).resolve().parents[1]
WRAPPERS = REPO / "wrappers"
PLAN_PY = REPO / "examples" / "nplb" / "campaign_plan.py"

A2A = "blink/alltoall/alltoall_comm_only.py"
AGTR = "blink/allgather/allgather_comm_only.py"
ARDC = "blink/allreduce/allreduce_b.py"
PROGRAM_OF = {A2A: "alltoall_comm_only", AGTR: "allgather_comm_only", ARDC: "allreduce_b"}
COMMON = "blink/_microbench_common.py"

HEADER = "Average,Minimum,Maximum,Median,MainRank"
METRICS = ["Avg-Duration", "Min-Duration", "Max-Duration", "Median-Duration", "MainRank-Duration"]
GPU_LINE = "GPU mode: backend CUDA, 8 device(s) visible; MPI reports GPU support: yes"
UCX_WARN = "[1699999999.123456] [node:1234:0] ucp_context.c:1: WARN something"
ROWS = [
    "0.000123,0.000100,0.000200,0.000120,0.000125",
    "0.000124,0.000101,0.000201,0.000121,0.000126",
    "0.000125,0.000102,0.000202,0.000122,0.000127",
    "0.000126,0.000103,0.000203,0.000123,0.000128",
    "0.000127,0.000104,0.000204,0.000124,0.000129",
]


def _run_planner(out: Path, *args: str) -> None:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "NPLB_WORK", "NPLB_NOW", "PAGE_LINES", "PAGE_COLS"):
        env.pop(var, None)
    env["WORK"] = str(out.parents[1] / "work")
    env["NPLB_REPORT_DIR"] = str(out.parents[1] / "reports")
    result = subprocess.run(
        [sys.executable, str(PLAN_PY), "--out", str(out), *args],
        env=env,
        cwd=out.parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def blocks(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    """The lab block files (15) and the --local ones (6), each written by the planner CLI."""
    root = tmp_path_factory.mktemp("campaign_wrappers")
    lab, local = root / "local" / "lab", root / "local" / "laptop"
    _run_planner(lab)
    _run_planner(local, "--local")
    found = {"lab": sorted(lab.glob("*.json")), "local": sorted(local.glob("*.json"))}
    assert len(found["lab"]) == 15 and len(found["local"]) == 6
    return found


def _apps(files: list[Path]) -> list[tuple[str, dict[str, Any]]]:
    """(block file name, app dict) of every app of every experiment of the files."""
    found = []
    for path in files:
        config = json.loads(path.read_text(encoding="utf-8"))
        for body in config["experiments"].values():
            found.extend((path.name, app) for app in body["apps"].values())
    return found


def _wrapper_class(relative: str) -> Any:
    """The `app` class of a wrapper file, imported the way CRAB loads it (by file path)."""
    name = "nplb_guard_" + re.sub(r"\W", "_", relative)
    spec = importlib.util.spec_from_file_location(name, WRAPPERS / relative)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


def _stdout(rows: list[str], trailer: str | None, preamble: bool = True) -> str:
    lines = [GPU_LINE, UCX_WARN] if preamble else []
    lines += [HEADER, *rows]
    if trailer is not None:
        lines.append(trailer)
    return "\n".join(lines) + "\n"


def _parse(relative: str, stdout: str) -> list[dict[str, float]]:
    wrapper = _wrapper_class(relative)(0, True, "")
    wrapper.stdout = stdout
    rows: list[dict[str, float]] = wrapper.read_data()
    return rows


# --- the blocks name real wrappers -------------------------------------------------------------


def test_every_block_wrapper_exists_and_names_its_program(blocks: dict[str, list[Path]]) -> None:
    seen: set[str] = set()
    for kind, files in blocks.items():
        for name, app in _apps(files):
            where = f"{kind} block {name}"
            path = app["path"]
            # wrappers/ may be a symlink to ../crab-wrappers, so the path is checked, not resolved
            assert not Path(path).is_absolute() and ".." not in Path(path).parts, f"{where}: {path}"
            wrapper = WRAPPERS / path
            assert wrapper.is_file(), f"{where}: no wrapper at wrappers/{path}"
            assert path in PROGRAM_OF, f"{where}: unexpected wrapper {path}"
            seen.add(path)
            source = wrapper.read_text(encoding="utf-8")
            assert "class app(microbench)" in source, where
            assert re.findall(r'get_path\("([^"]+)"\)', source) == [PROGRAM_OF[path]], where
            if kind == "lab":
                assert Path(app["binary"]).name == PROGRAM_OF[path], f"{where}: {app['binary']}"
            else:
                assert "binary" not in app, where
            if not app["collect"]:  # the aggressor is always the all-to-all
                assert path == A2A, where
    assert seen == {A2A, AGTR, ARDC}


def test_wrapper_files_match_the_crab_wrappers_checkout_when_present() -> None:
    sibling = REPO.parent / "crab-wrappers"
    if not (sibling / "blink").is_dir():
        pytest.skip(f"no crab-wrappers checkout next to the repo ({sibling}); nothing to compare")
    for relative in (A2A, AGTR, ARDC, COMMON):
        original = sibling / relative
        assert original.is_file(), f"../crab-wrappers has no {relative}"
        assert original.read_bytes() == (WRAPPERS / relative).read_bytes(), (
            f"wrappers/{relative} differs from ../crab-wrappers/{relative}"
        )


# --- the shared Blink parser -------------------------------------------------------------------


@pytest.mark.parametrize("relative", [A2A, AGTR, ARDC], ids=["a2a", "agtr", "ardc"])
def test_read_data_parses_each_victim_with_gpu_preamble(relative: str) -> None:
    out = _stdout(ROWS, "Ran 7 iterations. Measured 5 iterations.")
    assert out.splitlines()[:2] == [GPU_LINE, UCX_WARN]
    data = _parse(relative, out)
    assert len(data) == 5
    assert all(list(row) == METRICS for row in data)
    assert list(data[0].values()) == [0.000123, 0.000100, 0.000200, 0.000120, 0.000125]
    assert all(isinstance(value, float) for value in data[0].values())
    assert data[4]["MainRank-Duration"] == 0.000129
    # the same table without any preamble parses to the same rows
    assert (
        _parse(relative, _stdout(ROWS, "Ran 7 iterations. Measured 5 iterations.", False)) == data
    )


@pytest.mark.parametrize("relative", [A2A, AGTR, ARDC], ids=["a2a", "agtr", "ardc"])
def test_read_data_skips_warning_lines_inside_the_table(relative: str) -> None:
    rows = [*ROWS[:2], UCX_WARN, *ROWS[2:]]
    data = _parse(relative, _stdout(rows, "Ran 7 iterations. Measured 5 iterations."))
    assert len(data) == 5
    assert data[2]["Avg-Duration"] == 0.000125


def test_read_data_row_count_must_match_the_trailer() -> None:
    trailer = "Ran 7 iterations. Measured 5 iterations."
    with pytest.raises(ValueError, match="table has 5 rows but the benchmark measured 6"):
        _parse(ARDC, _stdout(ROWS, "Ran 8 iterations. Measured 6 iterations."))
    with pytest.raises(ValueError, match="Ran N iterations"):
        _parse(ARDC, _stdout(ROWS, None))
    no_header = "\n".join([GPU_LINE, UCX_WARN, *ROWS, trailer]) + "\n"
    with pytest.raises(ValueError, match=re.escape(HEADER)):
        _parse(ARDC, no_header)
    with pytest.raises(ValueError, match="expected 5 comma-separated numbers"):
        _parse(ARDC, _stdout([*ROWS[:4], "0.1,0.2,0.3,0.4"], trailer))


# --- the table holds every measured sample -----------------------------------------------------


def test_maxsamples_equals_iter_in_every_victim_app(blocks: dict[str, list[Path]]) -> None:
    collected = 0
    for kind, files in blocks.items():
        for name, app in _apps(files):
            words = app["args"].split()
            where = f"{kind} block {name}: {app['args']}"
            if app["collect"]:
                collected += 1
                assert "-iter" in words and "-maxsamples" in words, where
                assert words[words.index("-maxsamples") + 1] == words[words.index("-iter") + 1], (
                    where
                )
            else:
                assert "-iter" not in words and "-maxsamples" not in words, where
    assert collected > 0
