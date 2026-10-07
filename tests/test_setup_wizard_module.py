"""
`crab setup` module install (choice 3) must not propose the recipe id as the executable.

The recipe id (`qe-v7`, `blink`, `g500`) is not a command: pressing Enter used to register a
receipt whose binary does not exist. A recipe that knows the executable its module provides
offers it as the default; otherwise the wizard asks with no default and re-asks on an empty
answer.
"""

import pytest

import crab.setup.wizard as wizard
from crab.setup.recipes.blink import BlinkRecipe
from crab.setup.recipes.g500 import G500Recipe
from crab.setup.recipes.qe_v6 import QERecipeV6
from crab.setup.recipes.qe_v7 import QERecipeV7


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def _run_module_install(monkeypatch, recipe, answers):
    """Drive the wizard through a module install; return (saved receipts, prompts seen)."""
    saved = {}
    prompts = []
    queue = list(answers)

    def fake_ask(prompt, *args, default=..., choices=None, **kwargs):
        prompts.append((prompt, default))
        answer = queue.pop(0)
        if answer == "" and default is not ...:
            return default
        return answer

    monkeypatch.setattr(wizard.Prompt, "ask", fake_ask)
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: None)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")

    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    assert queue == [], "the wizard asked fewer questions than the test scripted"
    return saved, prompts


def test_qe_module_install_defaults_to_pw_x(monkeypatch):
    saved, _ = _run_module_install(monkeypatch, QERecipeV7(), ["3", "module load qe/7.4", ""])

    receipt = saved["qe-v7"]
    assert receipt["type"] == "module"
    assert receipt["binary_path"] == "pw.x"
    assert receipt["hooks"]["pre_run"][0] == "module load qe/7.4"


def test_blink_module_install_reasks_on_empty_answer(monkeypatch):
    saved, prompts = _run_module_install(
        monkeypatch, BlinkRecipe(), ["3", "module load blink", "", "ping-pong_b"]
    )

    exe_prompts = [p for p in prompts if "executable" in p[0].lower()]
    assert len(exe_prompts) == 2, "an empty answer must be refused and asked again"
    assert all(default is ... for _, default in exe_prompts), "no default for blink"
    assert saved["blink"]["binary_path"] == "ping-pong_b"


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        (QERecipeV6(), "pw.x"),
        (QERecipeV7(), "pw.x"),
        (G500Recipe(), "graph500_reference_bfs"),
        (BlinkRecipe(), ""),
    ],
)
def test_recipes_declare_their_module_executable(recipe, expected):
    assert recipe.module_executable == expected


def test_base_fast_search_looks_up_the_module_executable_on_path(monkeypatch, tmp_path):
    """The base auto-detect searched PATH for the recipe id; it must search for the command."""
    from crab.setup.recipes import base

    class _Recipe(base.BenchmarkRecipe):
        name = "Demo"
        benchmark_id = "demo-v1"
        module_executable = "demo.x"

        def check_dependencies(self, env):
            return True, ""

        def download_and_build(self, target_dir, params, env, log_callback=None):
            return False, None, ""

        def verify_existing(self, path):
            return path == "/opt/demo/bin/demo.x"

    looked_up = []

    def fake_which(cmd):
        looked_up.append(cmd)
        return "/opt/demo/bin/demo.x" if cmd == "demo.x" else None

    monkeypatch.setattr(base.shutil, "which", fake_which)
    assert _Recipe().fast_search(str(tmp_path)) == "/opt/demo/bin/demo.x"
    assert looked_up == ["demo.x"]
