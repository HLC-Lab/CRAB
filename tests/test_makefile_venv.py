"""`make venv` reuses an existing venv instead of building one again, and `make clean` only
deletes the in-tree `.venv`. Real `make` processes against a copy of the Makefile in a tmp dir,
with stub venvs: no network, and never a real `python3 -m venv` (a test either has a stub venv
present or only reads the recipe text through `make -n`)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"

ACTIVATE = "# activate\n"
ACTIVATE_FISH = "# activate.fish\n"

MARKER = "crab: clear PYTHONPATH"
REASON = (
    "# crab: clear PYTHONPATH (a cluster Python module sets it and breaks the venv's pip; "
    "deactivate does not restore it)\n"
)
BASH_BLOCK = REASON + "unset PYTHONPATH PYTHONHOME\n"
FISH_BLOCK = REASON + "set -e PYTHONPATH PYTHONHOME\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tmp dir holding a copy of the Makefile."""
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MAKEFILE, project / "Makefile")
    return project


def _make(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(project), *args], capture_output=True, text=True, check=False
    )


def _stub_venv(venv: Path, marked: bool = True) -> None:
    """An executable python and pip stub plus activate files with known content; `marked` adds
    the PYTHONPATH reset blocks, as a venv built or repaired by `make venv` carries them."""
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", "pip"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    (bin_dir / "activate").write_text(ACTIVATE + (BASH_BLOCK if marked else ""))
    (bin_dir / "activate.fish").write_text(ACTIVATE_FISH + (FISH_BLOCK if marked else ""))


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_an_existing_venv_is_reused(project: Path) -> None:
    _stub_venv(project / ".venv")
    before = _tree(project / ".venv")

    result = _make(project, "venv")

    assert result.returncode == 0, result.stderr
    assert "using the existing venv .venv" in result.stdout
    assert (project / ".venv/bin/activate").read_text() == ACTIVATE + BASH_BLOCK
    assert (project / ".venv/bin/activate.fish").read_text() == ACTIVATE_FISH + FISH_BLOCK
    assert _tree(project / ".venv") == before


def test_a_venv_outside_the_checkout_is_reused_and_left_by_clean(
    project: Path, tmp_path: Path
) -> None:
    external = tmp_path / "ext"
    _stub_venv(external)

    reused = _make(project, "venv", f"VENV_DIR={external}")
    assert reused.returncode == 0, reused.stderr
    assert f"using the existing venv {external}" in reused.stdout
    assert (external / "bin/activate").read_text() == ACTIVATE + BASH_BLOCK

    cleaned = _make(project, "clean", f"VENV_DIR={external}")
    assert cleaned.returncode == 0, cleaned.stderr
    assert f"leaving the venv {external} in place" in cleaned.stdout
    assert (external / "bin/python").exists()


def test_clean_removes_only_the_default_venv_and_build_artifacts(
    project: Path, tmp_path: Path
) -> None:
    def artifacts() -> None:
        for name in ("build", "dist", "x.egg-info"):
            (project / name).mkdir()

    _stub_venv(project / ".venv")
    artifacts()
    result = _make(project, "clean")
    assert result.returncode == 0, result.stderr
    for name in (".venv", "build", "dist", "x.egg-info"):
        assert not (project / name).exists(), name

    external = tmp_path / "ext"
    _stub_venv(external)
    artifacts()
    result = _make(project, "clean", f"VENV_DIR={external}")
    assert result.returncode == 0, result.stderr
    for name in ("build", "dist", "x.egg-info"):
        assert not (project / name).exists(), name
    assert (external / "bin/python").exists()


def test_the_venv_recipe_has_no_recursive_make(project: Path) -> None:
    """`make -n` runs any recipe line containing $(MAKE), so a dry run would build a venv."""
    result = _make(project, "-n", "venv")
    assert result.returncode == 0, result.stderr
    assert not (project / ".venv").exists()

    source = MAKEFILE.read_text().splitlines()
    start = source.index("venv: _check_python")
    recipe: list[str] = []
    for line in source[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line)
    assert recipe
    assert not any("$(MAKE)" in line for line in recipe)


def test_the_first_run_still_builds_a_venv_recipe(project: Path) -> None:
    result = _make(project, "-n", "venv")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "python3 -m venv .venv" in out
    assert ".venv/bin/pip install --upgrade pip" in out
    assert ".venv/bin/pip install argcomplete" in out
    assert "register-python-argcomplete crab" in out
    assert "'eval \"$(register-python-argcomplete crab)\"' >> .venv/bin/activate " in out
    assert (
        "'register-python-argcomplete --shell fish crab | source' >> .venv/bin/activate.fish" in out
    )


def test_an_unmarked_existing_venv_is_repaired_once(project: Path) -> None:
    _stub_venv(project / ".venv", marked=False)
    activate = project / ".venv/bin/activate"
    fish = project / ".venv/bin/activate.fish"

    first = _make(project, "venv")

    assert first.returncode == 0, first.stderr
    assert "using the existing venv .venv" in first.stdout
    assert "added the PYTHONPATH reset to .venv/bin/activate\n" in first.stdout
    assert "added the PYTHONPATH reset to .venv/bin/activate.fish" in first.stdout
    assert activate.read_text() == ACTIVATE + BASH_BLOCK
    assert fish.read_text() == ACTIVATE_FISH + FISH_BLOCK

    second = _make(project, "venv")

    assert second.returncode == 0, second.stderr
    assert "added" not in second.stdout
    assert activate.read_text() == ACTIVATE + BASH_BLOCK
    assert fish.read_text() == ACTIVATE_FISH + FISH_BLOCK


def test_sourcing_activate_clears_pythonpath_and_pythonhome(project: Path) -> None:
    _stub_venv(project / ".venv", marked=False)
    repaired = _make(project, "venv")
    assert repaired.returncode == 0, repaired.stderr
    activate = (project / ".venv/bin/activate").resolve()

    sourced = subprocess.run(
        [
            "env",
            "PYTHONPATH=/x",
            "PYTHONHOME=/y",
            "bash",
            "-c",
            f'. {activate}; echo "[${{PYTHONPATH-unset}}][${{PYTHONHOME-unset}}]"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout.strip() == "[unset][unset]"


def test_a_new_venv_recipe_appends_the_reset(project: Path) -> None:
    result = _make(project, "-n", "venv")
    assert result.returncode == 0, result.stderr
    assert not (project / ".venv").exists()
    assert MARKER in result.stdout
    assert "unset PYTHONPATH PYTHONHOME" in result.stdout
    assert "set -e PYTHONPATH PYTHONHOME" in result.stdout


def test_repair_skips_a_venv_without_an_activate_fish(project: Path) -> None:
    _stub_venv(project / ".venv", marked=False)
    (project / ".venv/bin/activate.fish").unlink()

    result = _make(project, "venv")

    assert result.returncode == 0, result.stderr
    assert (project / ".venv/bin/activate").read_text() == ACTIVATE + BASH_BLOCK
    assert not (project / ".venv/bin/activate.fish").exists()


def test_repair_creates_no_file_in_a_venv_without_activate(project: Path) -> None:
    """The stub venv of tests/test_makefile_wrappers.py has python and pip only."""
    bin_dir = project / ".venv/bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", "pip"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    before = _tree(project / ".venv")

    result = _make(project, "venv")

    assert result.returncode == 0, result.stderr
    assert _tree(project / ".venv") == before
