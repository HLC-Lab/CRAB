"""`crab setup` stores a typed binary path as an absolute path.

A relative path is made absolute from the folder the person is in while typing (a stored
relative path would be read at run time from a folder nobody chose), `~` is expanded, and a bare
command name (a module receipt) stays as it is. A value that cannot be resolved is explained and
asked again.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import crab.setup.wizard as wizard
from crab.setup.recipes.base import BenchmarkRecipe, BuildManifest


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class _Recipe(BenchmarkRecipe):
    name = "Demo"
    benchmark_id = "demo"

    def __init__(self):
        self.verified: list[str] = []

    @property
    def build_manifest(self):
        return BuildManifest(requires_modules=False)

    def check_dependencies(self, env):
        return True, ""

    def download_and_build(self, target_dir, params, env, log_callback=None):
        raise AssertionError("not built in these tests")

    def verify_existing(self, path):
        self.verified.append(path)
        return True


@pytest.fixture
def home(monkeypatch, tmp_path):
    """HOME and the cwd are folders of their own; the first chdir makes teardown restore the cwd."""
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    work = tmp_path / "work"
    (home / "bin").mkdir(parents=True)
    (work / "rel").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    return home


def _script(monkeypatch, answers):
    """Answer `Prompt.ask` from a queue; return (saved receipts, red messages printed)."""
    saved: dict = {}
    printed: list[str] = []
    queue = list(answers)

    def fake_ask(prompt, *args, default=..., choices=None, **kwargs):
        answer = queue.pop(0)
        if answer == "" and default is not ...:
            return default
        return answer

    monkeypatch.setattr(wizard.Prompt, "ask", fake_ask)
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: False)
    monkeypatch.setattr(wizard.questionary, "select", lambda *a, **k: _Answer("mpirun"))
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: None)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")
    monkeypatch.setattr(wizard.console, "print", lambda *a, **k: printed.append(str(a[0])))
    return saved, printed, queue


def _custom(monkeypatch, path_answers):
    saved, printed, queue = _script(monkeypatch, ["Demo", *path_answers, ""])
    wizard._run_custom_benchmark_wizard([], [])
    assert queue == [], "the wizard asked fewer questions than the test scripted"
    return saved["demo"], printed


def _recipe_flow(monkeypatch, answers):
    recipe = _Recipe()
    saved, printed, queue = _script(monkeypatch, answers)
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    assert queue == [], "the wizard asked fewer questions than the test scripted"
    return recipe, saved, printed


def test_custom_benchmark_stores_a_relative_path_as_absolute(monkeypatch, home):
    Path("rel/x").write_text("")
    receipt, _ = _custom(monkeypatch, ["rel/x"])
    assert receipt["binary_path"] == os.path.abspath("rel/x")


def test_custom_benchmark_expands_a_tilde_path(monkeypatch, home):
    (home / "bin" / "x").write_text("")
    receipt, _ = _custom(monkeypatch, ["~/bin/x"])
    assert receipt["binary_path"] == f"{home}/bin/x"


def test_custom_benchmark_asks_again_after_an_environment_variable(monkeypatch, home):
    (home / "bin" / "x").write_text("")
    receipt, printed = _custom(monkeypatch, ["$HOME/bin/x", "~/bin/x"])
    assert receipt["binary_path"] == f"{home}/bin/x"
    assert any("environment variables" in line for line in printed)


def test_manual_path_gives_the_recipe_an_absolute_path(monkeypatch, home):
    recipe, saved, _ = _recipe_flow(monkeypatch, ["2", "~/bin/x"])
    assert recipe.verified == [f"{home}/bin/x"]
    assert saved["demo"]["binary_path"] == f"{home}/bin/x"

    recipe, saved, _ = _recipe_flow(monkeypatch, ["2", "rel/x"])
    assert recipe.verified == [os.path.abspath("rel/x")]
    assert saved["demo"]["binary_path"] == os.path.abspath("rel/x")


def test_manual_path_asks_again_after_an_unresolvable_value(monkeypatch, home):
    recipe, saved, printed = _recipe_flow(monkeypatch, ["2", "$HOME/x", "~nosuchuser_zz/x", "q"])
    assert recipe.verified == []
    assert saved == {}
    assert sum("full path" in line for line in printed) >= 2


def test_module_executable_bare_name_stays_bare(monkeypatch, home):
    _, saved, _ = _recipe_flow(monkeypatch, ["3", "module load qe/7.4", "pw.x"])
    receipt = saved["demo"]
    assert receipt["type"] == "module"
    assert receipt["binary_path"] == "pw.x"


def test_module_executable_typed_with_a_tilde_becomes_absolute(monkeypatch, home):
    _, saved, _ = _recipe_flow(monkeypatch, ["3", "module load qe/7.4", "~/bin/pw.x"])
    assert saved["demo"]["binary_path"] == f"{home}/bin/pw.x"
