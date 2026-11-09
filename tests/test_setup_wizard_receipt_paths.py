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


class _IdPrompt:
    """What the faked `questionary.text` saw: its calls and the answers its validator refused."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.refused: list[str] = []


def _fake_id_prompt(monkeypatch, typed):
    """Fake `questionary.text`: type the queued answers (the default once the queue is empty).

    Like the real prompt, an answer the `validate` callable refuses is not returned: the next
    queued answer is typed instead.
    """
    seen = _IdPrompt()
    queue = list(typed)

    def fake_text(message, *args, default="", validate=None, **kwargs):
        seen.calls.append({"message": message, "default": default, "validate": validate})
        while True:
            answer = queue.pop(0) if queue else default
            verdict = True if validate is None else validate(answer)
            if verdict is True:
                return _Answer(answer)
            seen.refused.append(answer)

    monkeypatch.setattr(wizard.questionary, "text", fake_text)
    return seen


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
    _fake_id_prompt(monkeypatch, [])
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


def test_custom_benchmark_a_bare_name_means_a_file_in_the_current_folder(monkeypatch, home):
    Path("build").write_text("")
    receipt, _ = _custom(monkeypatch, ["build"])
    assert receipt["binary_path"] == os.path.abspath("build")


def test_manual_path_a_bare_name_is_made_absolute(monkeypatch, home):
    recipe, saved, _ = _recipe_flow(monkeypatch, ["2", "build"])
    assert recipe.verified == [os.path.abspath("build")]
    assert saved["demo"]["binary_path"] == os.path.abspath("build")


def test_auto_detected_relative_path_is_stored_absolute(monkeypatch, home):
    recipe = _Recipe()
    recipe.fast_search = lambda _dir: "bin/x"
    saved, _, queue = _script(monkeypatch, ["1"])
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    assert queue == []
    assert saved["demo"]["binary_path"] == os.path.abspath("bin/x")


def _fake_launcher_select(monkeypatch, pick):
    """Fake the launcher `questionary.select`; `pick` is a value, or None for the default choice."""
    asked: list[dict] = []

    def fake_select(message, *args, choices=None, default=None, **kwargs):
        asked.append({"choices": choices, "default": default})
        return _Answer(choices[0].value if pick is None else pick)

    monkeypatch.setattr(wizard.questionary, "select", fake_select)
    return asked


def _custom_with_id(monkeypatch, display_name, typed_ids, launcher=None):
    Path("x").write_text("")
    saved, _, queue = _script(monkeypatch, [display_name, "x", ""])
    prompt = _fake_id_prompt(monkeypatch, typed_ids)
    asked = _fake_launcher_select(monkeypatch, launcher)
    wizard._run_custom_benchmark_wizard([], [])
    assert queue == []
    return saved, prompt, asked


def test_the_custom_wizard_asks_for_the_benchmark_id_with_the_derived_default(monkeypatch, home):
    derived = wizard._derive_benchmark_id("My Bench 2")
    saved, prompt, _ = _custom_with_id(monkeypatch, "My Bench 2", [])
    assert len(prompt.calls) == 1
    assert prompt.calls[0]["default"] == derived
    assert list(saved) == [derived]
    assert saved[derived]["id"] == derived


def test_an_id_that_is_not_a_clean_slug_is_asked_again(monkeypatch, home):
    saved, prompt, _ = _custom_with_id(monkeypatch, "My Bench", ["My Bench!", "", "my_bench_x"])
    assert prompt.refused == ["My Bench!", ""]
    assert list(saved) == ["my_bench_x"]


def test_the_launcher_question_lists_no_override_first(monkeypatch, home):
    saved, _, asked = _custom_with_id(monkeypatch, "Demo", [])
    assert len(asked) == 1
    assert [choice.value for choice in asked[0]["choices"]] == ["", "srun", "mpirun"]
    assert saved["demo"]["launcher_override"] == ""


def test_the_saved_receipt_carries_the_entered_id_and_the_launcher(monkeypatch, home):
    saved, _, _ = _custom_with_id(monkeypatch, "My Bench", ["mybench"], launcher="srun")
    assert list(saved) == ["mybench"]
    assert saved["mybench"]["id"] == "mybench"
    assert saved["mybench"]["launcher_override"] == "srun"

    saved, _, _ = _custom_with_id(monkeypatch, "My Bench", ["mybench"], launcher="mpirun")
    assert saved["mybench"]["launcher_override"] == "mpirun"
