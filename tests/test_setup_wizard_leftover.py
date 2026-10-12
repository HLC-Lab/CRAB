"""
`crab setup` build from source (choice 4) with a leftover `benchmarks/<id>` folder.

A failed build leaves the folder behind and writes no receipt, so the next `git clone` into
it fails. The wizard asks before building again: yes removes the folder, no skips the recipe.
A folder owned by a `source` receipt keeps going through the older `handle_cleanup` prompt.
"""

import os

import crab.setup.wizard as wizard
from crab.setup.recipes.base import BenchmarkRecipe, BuildManifest, BuildResult


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class _FakeRecipe(BenchmarkRecipe):
    name = "Demo"
    benchmark_id = "demo"

    def __init__(self):
        self.built_in = []
        self.dir_existed_at_build = []

    @property
    def build_manifest(self):
        return BuildManifest(requires_modules=False)

    def check_dependencies(self, env):
        return True, ""

    def download_and_build(self, target_dir, params, env, log_callback=None):
        self.built_in.append(target_dir)
        self.dir_existed_at_build.append(os.path.exists(target_dir))
        return True, BuildResult(binary_path=os.path.join(target_dir, "bin")), ""

    def verify_existing(self, path):
        return False


def _drive(monkeypatch, tmp_path, recipe, confirm_answers, receipt=None):
    """Run option 4 for `recipe`; return (saved receipts, confirm prompts seen)."""
    saved = {}
    confirms = []
    queue = list(confirm_answers)

    def fake_confirm(prompt, *args, **kwargs):
        confirms.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(wizard, "BENCHMARKS_DIR", str(tmp_path))
    monkeypatch.setattr(wizard.Prompt, "ask", lambda *a, **k: "4")
    monkeypatch.setattr(wizard.Confirm, "ask", fake_confirm)
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: receipt)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")

    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    assert queue == [], "the wizard asked fewer confirms than the test scripted"
    return saved, confirms


def _leftover(tmp_path):
    leftover = tmp_path / "demo"
    leftover.mkdir()
    (leftover / "half-built.txt").write_text("x")
    return leftover


def test_leftover_build_is_removed_before_building_when_user_says_yes(monkeypatch, tmp_path):
    leftover = _leftover(tmp_path)
    recipe = _FakeRecipe()

    saved, confirms = _drive(monkeypatch, tmp_path, recipe, [True])

    assert len(confirms) == 1
    assert "leftover build" in confirms[0]
    assert str(leftover) in confirms[0]
    assert recipe.built_in == [str(leftover)]
    assert recipe.dir_existed_at_build == [False]
    assert saved["demo"]["type"] == "source"
    assert saved["demo"]["binary_path"] == os.path.join(str(leftover), "bin")


def test_leftover_build_is_kept_and_recipe_skipped_when_user_says_no(monkeypatch, tmp_path):
    leftover = _leftover(tmp_path)
    recipe = _FakeRecipe()

    saved, confirms = _drive(monkeypatch, tmp_path, recipe, [False])

    assert len(confirms) == 1
    assert recipe.built_in == []
    assert (leftover / "half-built.txt").read_text() == "x"
    assert saved == {}


def test_source_receipt_keeps_the_existing_cleanup_prompt_only(monkeypatch, tmp_path):
    leftover = _leftover(tmp_path)
    recipe = _FakeRecipe()
    receipt = {"id": "demo", "type": "source", "binary_path": str(leftover / "bin")}

    # First confirm: "reconfigure?" (existing receipt); second: handle_cleanup; none after.
    saved, confirms = _drive(monkeypatch, tmp_path, recipe, [True, True], receipt=receipt)

    assert len(confirms) == 2
    assert "reconfigure" in confirms[0]
    assert "Found old build path" in confirms[1]
    assert recipe.dir_existed_at_build == [False]
    assert "demo" in saved


def test_no_leftover_means_no_extra_prompt(monkeypatch, tmp_path):
    recipe = _FakeRecipe()

    saved, confirms = _drive(monkeypatch, tmp_path, recipe, [])

    assert confirms == []
    assert recipe.built_in == [str(tmp_path / "demo")]
    assert "demo" in saved


def test_symlinked_leftover_is_never_deleted(monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("x")
    benchmarks = tmp_path / "benchmarks"
    benchmarks.mkdir()
    (benchmarks / "demo").symlink_to(elsewhere)
    recipe = _FakeRecipe()

    saved, confirms = _drive(monkeypatch, benchmarks, recipe, [])

    assert recipe.built_in == []
    assert (elsewhere / "keep.txt").read_text() == "x"
    assert saved == {}
