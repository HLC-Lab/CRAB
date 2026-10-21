"""The examples folder is a guide: every set explains itself and every config checks clean.

Before: `examples/` had no README, `examples/lorenzo/` was named after a person, and nothing
checked the configs against the preset a reader would run them with (the shipped
`test_every_shipped_example_passes_the_checks` runs without one, and
`test_local_examples_fit_the_local_preset` ignores the warnings it returns).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from crab import paths
from crab.core.config_checks import check_config
from crab.core.execution.settings import from_preset

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"

# The shipped preset each set is meant to run with. A set not listed here is checked without one.
_PRESET_OF_SET = {
    "local": "local",
    "cluster_di": "cluster_di",
    "leonardo": "leonardo",
}

# Warnings an example may give, with the reason its README states. Everything else must be silent.
_EXPECTED_WARNINGS: dict[str, list[str]] = {
    "examples/leonardo/congestion/corun_nics.json": [
        f"experiment {exp}: launcher flag '--cpu-bind=socket' is replaced by CRAB's core binding "
        "for apps that share nodes or set cpus"
        for exp in ("01_victim_alone", "02_corun_separate_nics", "03_corun_same_nic")
    ],
}


def _tracked_example_files() -> list[Path]:
    """Files under `examples/`, from git when REPO is a checkout, else from the filesystem.

    Untracked leftovers (caches, scratch folders) must not decide whether the suite passes.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "examples"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):  # no git, or an sdist without .git
        return [
            f
            for f in EXAMPLES.rglob("*")
            if f.is_file() and "__pycache__" not in f.relative_to(EXAMPLES).parts
        ]
    return [REPO / name for name in out.split("\0") if name]


def _readme_dirs() -> list[Path]:
    """The examples root and every folder that holds an example file."""
    dirs = {p for f in _tracked_example_files() for p in f.parents if EXAMPLES in p.parents}
    return [EXAMPLES, *sorted(dirs)]


def _example_files() -> list[Path]:
    return sorted(
        f for f in _tracked_example_files() if f.name != "README.md" and not f.name.startswith(".")
    )


@pytest.mark.parametrize("folder", _readme_dirs(), ids=lambda d: str(d.relative_to(REPO)))
def test_every_example_folder_has_a_readme(folder: Path) -> None:
    assert (folder / "README.md").is_file(), f"{folder.relative_to(REPO)} has no README.md"


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_example_file_is_named_in_its_folders_readme(path: Path) -> None:
    readme = path.parent / "README.md"
    assert readme.is_file(), f"{path.parent.relative_to(REPO)} has no README.md"
    assert path.name in readme.read_text(), f"{readme.relative_to(REPO)} never mentions {path.name}"


@pytest.mark.parametrize(
    "readme", sorted(EXAMPLES.rglob("README.md")), ids=lambda p: str(p.relative_to(REPO))
)
def test_readme_links_point_at_files_that_exist(readme: Path) -> None:
    for target in re.findall(r"\]\(([^)\s]+)\)", readme.read_text()):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        resolved = (readme.parent / target.split("#")[0]).resolve()
        assert resolved.exists(), f"{readme.relative_to(REPO)} links to missing {target}"


def test_nothing_in_the_repo_still_names_the_old_folder() -> None:
    old = "examples/" + "lorenzo"
    hits = [
        str(f.relative_to(REPO))
        for root in ("docs", "tests", "examples", "src/crab/webui/tests")
        for f in (REPO / root).rglob("*")
        if f.is_file()
        and f.suffix in {".md", ".py", ".ts", ".json"}
        and f != Path(__file__).resolve()
        and old in f.read_text(errors="ignore")
    ]
    assert hits == []


def _settings_of(preset: str):
    presets = json.loads(paths.shipped_presets().read_text())
    body = dict(presets[preset])
    body["env"] = {**presets["_common"].get("env", {}), **body.get("env", {})}
    return from_preset(body, preset)


def _config_files() -> list[Path]:
    return sorted(EXAMPLES.rglob("*.json"))


@pytest.mark.parametrize("path", _config_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_config_checks_clean_under_its_sets_preset(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = str(path.relative_to(REPO))
    preset = _PRESET_OF_SET.get(path.relative_to(EXAMPLES).parts[0])
    monkeypatch.setattr("os.cpu_count", lambda: 8)  # a small laptop; the local set must fit it
    settings = _settings_of(preset) if preset else None

    warnings = check_config(json.loads(path.read_text()), settings)

    assert warnings == _EXPECTED_WARNINGS.get(rel, [])
