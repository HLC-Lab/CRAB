"""
`crab setup` auto-detect (choice 1) deep search must look for the real executable.

It used to search ~ for a file named after the recipe id (`qe-v7`, `g500`), which is not a
command, so it never found an install. It now looks for the recipe's `module_executable`
and stores the directory that holds it, the same shape the fast search and the wrappers use
for a 'binary' receipt (the wrappers join the executable name onto it).
"""

import os

import crab.setup.wizard as wizard
from crab.setup.recipes.blink import BlinkRecipe
from crab.setup.recipes.g500 import G500Recipe
from crab.setup.recipes.qe_v7 import QERecipeV7


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def _make_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


def _run_deep_search_install(monkeypatch, tmp_path, recipe):
    """Drive the wizard through auto-detect with a deep search of a fake home; return receipts."""
    saved = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(type(recipe), "fast_search", lambda self, _dir: None)
    monkeypatch.setattr(wizard.Prompt, "ask", lambda *a, **k: "1")
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: None)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")

    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    return saved


def test_qe_deep_search_finds_pw_x_and_stores_its_directory(monkeypatch, tmp_path):
    _make_executable(tmp_path / "apps" / "qe" / "bin" / "pw.x")

    saved = _run_deep_search_install(monkeypatch, tmp_path, QERecipeV7())

    receipt = saved["qe-v7"]
    assert receipt["type"] == "binary"
    assert receipt["binary_path"] == os.path.join(str(tmp_path), "apps", "qe", "bin")


def test_g500_deep_search_finds_the_bfs_binary_and_stores_its_directory(monkeypatch, tmp_path):
    _make_executable(tmp_path / "graph500" / "src" / "graph500_reference_bfs")

    saved = _run_deep_search_install(monkeypatch, tmp_path, G500Recipe())

    assert saved["g500"]["binary_path"] == os.path.join(str(tmp_path), "graph500", "src")


def test_deep_search_ignores_a_file_named_after_the_recipe_id(monkeypatch, tmp_path):
    """A stray executable called `qe-v7` is not an install: nothing is registered."""
    _make_executable(tmp_path / "junk" / "qe-v7")

    saved = _run_deep_search_install(monkeypatch, tmp_path, QERecipeV7())

    assert saved == {}


def test_blink_deep_search_rejects_a_directory_the_recipe_does_not_accept(monkeypatch, tmp_path):
    """Blink declares no executable, so the id is searched; the hit must still pass the recipe."""
    _make_executable(tmp_path / "junk" / "blink")

    saved = _run_deep_search_install(monkeypatch, tmp_path, BlinkRecipe())

    assert saved == {}
