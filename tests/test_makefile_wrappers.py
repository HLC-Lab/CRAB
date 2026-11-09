"""The Makefile clones the crab-wrappers repository into wrappers/ when it is missing, between
`pip install -e .` and `crab setup`. Real `make` and `git` processes against a copy of the
Makefile in a tmp dir and a local bare repository: no network."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tmp dir holding a copy of the Makefile."""
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MAKEFILE, project / "Makefile")
    return project


@pytest.fixture
def origin(tmp_path: Path) -> str:
    """A file:// URL of a bare repository with one commit."""
    bare, work = tmp_path / "wrappers.git", tmp_path / "wrappers-work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("w\n")
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*git, "push", "-q", str(bare), "main"], check=True)
    return f"file://{bare}"


def _make(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(project), *args], capture_output=True, text=True, check=False
    )


def test_wrappers_are_cloned_when_missing_and_left_alone_when_present(project, origin) -> None:
    first = _make(project, "_wrappers", f"WRAPPERS_URL={origin}")
    assert first.returncode == 0, first.stderr
    assert (project / "wrappers" / ".git").exists()
    assert (project / "wrappers" / "README.md").read_text() == "w\n"

    (project / "wrappers" / "marker").write_text("keep\n")
    second = _make(project, "_wrappers", "WRAPPERS_URL=file:///nonexistent/never-cloned")
    assert second.returncode == 0, second.stderr
    assert "already present" in second.stdout
    assert (project / "wrappers" / "marker").read_text() == "keep\n"


def test_the_install_clones_wrappers_after_pip_and_before_setup(project) -> None:
    result = _make(project, "-n", "_install_core")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()

    def at(needle: str) -> int:
        return next(i for i, line in enumerate(lines) if needle in line)

    assert at("pip install -e .") < at("git clone") < at("bin/crab setup")


def _stub_venv(project: Path) -> Path:
    """A stub venv: pip records its arguments and whether wrappers/ existed at that moment,
    crab drops a marker file when called. Returns the pip log path."""
    bin_dir = project / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    log = project / "pip.log"
    scripts = {
        "python": "#!/bin/sh\nexit 0\n",
        "pip": (
            "#!/bin/sh\n"
            f'echo "$@ wrappers-present=$([ -d "{project}/wrappers" ] && echo yes || echo no)"'
            f' >> "{log}"\n'
        ),
        "crab": f'#!/bin/sh\ntouch "{project}/crab-was-called"\n',
    }
    for name, body in scripts.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    return log


def test_make_core_installs_without_the_wizard(project) -> None:
    log = _stub_venv(project)
    (project / "wrappers").mkdir()

    result = _make(project, "core")

    assert result.returncode == 0, result.stderr
    assert "install -e ." in log.read_text()
    assert "wrappers/ already present" in result.stdout
    assert "CRAB core installed" in result.stdout
    assert "setup wizard" not in result.stdout
    assert not (project / "crab-was-called").exists()


def test_make_core_clones_missing_wrappers_after_pip(project, origin) -> None:
    log = _stub_venv(project)

    result = _make(project, "core", f"WRAPPERS_URL={origin}")

    assert result.returncode == 0, result.stderr
    assert log.read_text().strip() == "install -e . wrappers-present=no"
    assert (project / "wrappers" / "README.md").read_text() == "w\n"
    assert result.stdout.index("pip install -e .") < result.stdout.index("Cloning the wrappers")
    assert not (project / "crab-was-called").exists()


def _installed(project: Path) -> None:
    """Make the project look installed: an executable .venv/bin/crab stub."""
    stub = project / ".venv" / "bin" / "crab"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)


def test_rerunning_make_on_an_installed_checkout_clones_missing_wrappers(project, origin) -> None:
    _installed(project)
    result = _make(project, f"WRAPPERS_URL={origin}")
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert (project / "wrappers" / "README.md").read_text() == "w\n"


def test_a_failed_clone_on_an_installed_checkout_points_to_crab_update(project) -> None:
    _installed(project)
    result = _make(project, "WRAPPERS_URL=file:///nonexistent")
    assert result.returncode != 0
    assert "crab update" in result.stderr
    assert not (project / "wrappers" / "README.md").exists()
