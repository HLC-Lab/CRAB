"""A detached CRAB checkout keeps its own release line in `crab update`.

On `sbatchman`, product tags merged from `develop` are ancestors too, so the nearest tag of an
untagged commit can be a product tag. The line must come from the `+sbatchman` tags instead.
All against real throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from crab.cli.update import run_update


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _origin(tmp: Path, name: str, files: dict[str, str]) -> tuple[Path, Path]:
    """A bare origin plus a working repo that pushes to it."""
    bare, work = tmp / f"{name}.git", tmp / f"{name}-work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    return bare, work


def _commit(work: Path, rel: str, text: str, tag: str | None = None) -> str:
    (work / rel).write_text(text)
    git(work, "add", rel)
    git(work, "commit", "-q", "-m", f"change {rel}")
    if tag:
        git(work, "tag", tag)
    return git(work, "rev-parse", "HEAD")


class Pip:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, root: Path) -> None:
        self.calls.append(root)


CRAB_FILES = {"pyproject.toml": "deps = 1\n", "src/crab/__init__.py": '__version__ = "0.1.0"\n'}


class Topology(NamedTuple):
    install: Path
    wrappers_dir: Path
    wrappers_url: str
    sbatchman_untagged: str  # s2: on sbatchman, after the merge of the product tag v1.5.0
    product_untagged: str  # c3: on main, after v1.5.0
    sbatchman_tagged: str  # s1, tagged v1.4.0+sbatchman
    product_tagged: str  # c2, tagged v1.5.0
    sbatchman_newest: str  # s3, tagged v1.6.0+sbatchman
    product_newest: str  # c4, tagged v1.7.0


@pytest.fixture
def topology(tmp_path: Path) -> Topology:
    """main: c1 (v1.4.0) - three commits - c2 (v1.5.0) - c3 - c4 (v1.7.0)
    sbatchman: c1 - s1 (v1.4.0+sbatchman) - merge of v1.5.0 - s2 - s3 (v1.6.0+sbatchman)
    The product tag v1.5.0 is strictly nearer to s2 than v1.4.0+sbatchman."""
    bare, work = _origin(tmp_path, "crab", {**CRAB_FILES, "a.py": "0\n"})
    _commit(work, "a.py", "1\n", tag="v1.4.0")
    git(work, "branch", "sbatchman")
    for i in range(3):
        _commit(work, "a.py", f"1b{i}\n")
    product_tagged = _commit(work, "a.py", "2\n", tag="v1.5.0")
    product_untagged = _commit(work, "a.py", "3\n")
    product_newest = _commit(work, "a.py", "4\n", tag="v1.7.0")

    git(work, "checkout", "-q", "sbatchman")
    sbatchman_tagged = _commit(work, "s.py", "1\n", tag="v1.4.0+sbatchman")
    git(work, "merge", "-q", "--no-ff", "-m", "merge v1.5.0", "v1.5.0")
    sbatchman_untagged = _commit(work, "s.py", "2\n")
    sbatchman_newest = _commit(work, "s.py", "3\n", tag="v1.6.0+sbatchman")
    git(work, "push", "-q", "--tags", "origin", "main", "sbatchman")

    install = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(bare), str(install)], check=True)
    # The wrong pick this test is about: the nearest tag of s2 is the product tag.
    assert git(install, "describe", "--tags", "--abbrev=0", sbatchman_untagged) == "v1.5.0"

    wbare, wwork = _origin(tmp_path, "wrappers", {"README.md": "w\n"})
    _commit(wwork, "README.md", "w6\n", tag="v1.6.0")
    _commit(wwork, "README.md", "w7\n", tag="v1.7.0")
    git(wwork, "push", "-q", "--tags", "origin", "main")
    wdir = tmp_path / "wrappers-dir"
    subprocess.run(["git", "clone", "-q", str(wbare), str(wdir)], check=True)
    return Topology(
        install,
        wdir,
        str(wbare),
        sbatchman_untagged,
        product_untagged,
        sbatchman_tagged,
        product_tagged,
        sbatchman_newest,
        product_newest,
    )


def _update_from(t: Topology, commit: str) -> dict:
    git(t.install, "checkout", "-q", "--detach", commit)
    return run_update(t.install, t.wrappers_dir, t.wrappers_url, Pip())


def test_an_untagged_sbatchman_commit_stays_on_the_sbatchman_line(topology: Topology) -> None:
    result = _update_from(topology, topology.sbatchman_untagged)
    assert result["crab"]["new"] == "v1.6.0+sbatchman"
    assert git(topology.install, "rev-parse", "HEAD") == topology.sbatchman_newest


def test_an_untagged_product_commit_stays_on_the_product_line(topology: Topology) -> None:
    result = _update_from(topology, topology.product_untagged)
    assert result["crab"]["new"] == "v1.7.0"
    assert git(topology.install, "rev-parse", "HEAD") == topology.product_newest


def test_a_checkout_exactly_on_a_sbatchman_tag_stays_on_the_sbatchman_line(
    topology: Topology,
) -> None:
    result = _update_from(topology, topology.sbatchman_tagged)
    assert result["crab"]["new"] == "v1.6.0+sbatchman"
    assert git(topology.install, "rev-parse", "HEAD") == topology.sbatchman_newest


def test_a_checkout_exactly_on_a_product_tag_moves_to_the_newest_product_tag(
    topology: Topology,
) -> None:
    result = _update_from(topology, topology.product_tagged)
    assert result["crab"]["new"] == "v1.7.0"
    assert git(topology.install, "rev-parse", "HEAD") == topology.product_newest
