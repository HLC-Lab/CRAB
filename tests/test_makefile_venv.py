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


def _stub_venv(venv: Path) -> None:
    """An executable python and pip stub plus activate files with known content."""
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", "pip"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    (bin_dir / "activate").write_text(ACTIVATE)
    (bin_dir / "activate.fish").write_text(ACTIVATE_FISH)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_an_existing_venv_is_reused(project: Path) -> None:
    _stub_venv(project / ".venv")
    before = _tree(project / ".venv")

    result = _make(project, "venv")

    assert result.returncode == 0, result.stderr
    assert "using the existing venv .venv" in result.stdout
    assert (project / ".venv/bin/activate").read_text() == ACTIVATE
    assert (project / ".venv/bin/activate.fish").read_text() == ACTIVATE_FISH
    assert _tree(project / ".venv") == before


def test_a_venv_outside_the_checkout_is_reused_and_left_by_clean(
    project: Path, tmp_path: Path
) -> None:
    external = tmp_path / "ext"
    _stub_venv(external)

    reused = _make(project, "venv", f"VENV_DIR={external}")
    assert reused.returncode == 0, reused.stderr
    assert f"using the existing venv {external}" in reused.stdout
    assert (external / "bin/activate").read_text() == ACTIVATE

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
