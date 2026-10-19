"""
`crab setup` build from source (choice 4): a blank module line means "no modules".

The prompt used to carry `default="module load gcc openmpi"`, so Enter on a blank line loaded
modules the user never asked for. The example now lives in the prompt text only; a blank answer
skips the module capture and adds no module line to the receipt's pre-run hooks.

Nothing is built and no module system is touched: the recipe's dependency check and build, and
`capture_module_environment`, are faked.
"""

import crab.setup.wizard as wizard
from crab.setup.recipes.base import BuildResult
from crab.setup.recipes.blink import BlinkRecipe

_MODULE_PROMPT = "pre-build cluster module loads"


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def _run_source_build(monkeypatch, answer):
    """Drive choice 4 for Blink; return (saved receipt, module prompts, captured lines)."""
    recipe = BlinkRecipe()
    assert recipe.build_manifest.requires_modules
    saved = {}
    prompts = []
    captured = []
    queue = ["4", answer]

    def fake_ask(prompt, *args, default=..., choices=None, **kwargs):
        prompts.append((prompt, default))
        reply = queue.pop(0)
        if reply == "" and default is not ...:
            return default
        return reply

    def fake_capture(module_cmd):
        captured.append(module_cmd)
        return {"PATH": "/fake"}

    monkeypatch.setattr(wizard.Prompt, "ask", fake_ask)
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _Answer([recipe.suite]))
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: None)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")
    monkeypatch.setattr(wizard, "capture_module_environment", fake_capture)
    monkeypatch.setattr(wizard, "handle_cleanup", lambda _id: None)
    monkeypatch.setattr(wizard, "_clear_leftover_build", lambda _id, _name: True)
    monkeypatch.setattr(recipe, "check_dependencies", lambda env: (True, ""))
    monkeypatch.setattr(
        recipe,
        "download_and_build",
        lambda target_dir, params, env, log_callback=None: (
            True,
            BuildResult(binary_path="/fake/blink/bin"),
            "",
        ),
    )

    wizard._run_recipe_wizard([recipe], {recipe.suite: [recipe]}, [recipe.benchmark_id])
    assert queue == [], "the wizard asked fewer questions than the test scripted"
    module_prompts = [p for p in prompts if _MODULE_PROMPT in p[0]]
    return saved["blink"], module_prompts, captured


def _module_lines(receipt):
    return [h for h in receipt["hooks"]["pre_run"] if h.startswith("module ")]


def test_blank_module_answer_loads_no_modules(monkeypatch):
    receipt, _, captured = _run_source_build(monkeypatch, "")

    assert captured == [], "a blank answer must not load any module"
    assert _module_lines(receipt) == []
    assert receipt["binary_path"] == "/fake/blink/bin"


def test_module_prompt_shows_the_example_as_a_hint_not_a_default(monkeypatch):
    _, module_prompts, _ = _run_source_build(monkeypatch, "")

    assert len(module_prompts) == 1
    prompt, default = module_prompts[0]
    assert default is ..., "the module prompt must not pre-fill an answer"
    assert "module load gcc openmpi" in prompt


def test_module_answer_is_captured_and_saved_as_a_pre_run_hook(monkeypatch):
    receipt, _, captured = _run_source_build(monkeypatch, "module load cmake/3.27.9")

    assert captured == ["module load cmake/3.27.9"]
    assert _module_lines(receipt) == ["module load cmake/3.27.9"]
