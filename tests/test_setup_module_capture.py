"""
`crab setup` build from source: the pre-build module line must load or the recipe is skipped.

`capture_module_environment` used to fall back to the plain environment when the module
system could not be initialised, and the wizard saved the unverified line as a pre-run hook.
Now it tries the usual init scripts (modules.sh, Lmod) and raises `ModuleCaptureError` with
the command and the shell's error; the wizard prints it, skips the recipe, saves nothing.

The machine's real module system is never used: the init candidates point at tmp files that
define a fake `module` function.
"""

import os

import pytest

import crab.core.process.manager as manager
import crab.setup.wizard as wizard
from crab.setup.recipes.base import BenchmarkRecipe, BuildManifest, BuildResult

_FAKE_INIT = """\
module() {
    if [ "$1" = "load" ] && [ "$2" = "good" ]; then
        export PATH="%(bindir)s:$PATH"
        export FAKE_MODULE_LOADED=1
        return 0
    fi
    echo "ModuleCmd_Load: unknown module $2" >&2
    return 1
}
"""


@pytest.fixture
def fake_init(tmp_path, monkeypatch):
    """Primary init script absent, the Lmod-style candidate defines the fake `module`."""
    lmod = tmp_path / "lmod-init.sh"
    lmod.write_text(_FAKE_INIT % {"bindir": str(tmp_path / "bin")})
    monkeypatch.setattr(manager, "MODULE_INIT_SCRIPTS", (str(tmp_path / "missing.sh"), str(lmod)))
    monkeypatch.delenv("LMOD_PKG", raising=False)
    return lmod


def test_second_init_candidate_is_used_when_the_first_is_absent(fake_init, tmp_path):
    env = wizard.capture_module_environment("module load good")

    assert env["FAKE_MODULE_LOADED"] == "1"
    assert env["PATH"].startswith(str(tmp_path / "bin") + ":")


def test_lmod_pkg_init_is_the_last_candidate(monkeypatch, tmp_path):
    pkg = tmp_path / "lmod"
    (pkg / "init").mkdir(parents=True)
    (pkg / "init" / "bash").write_text(_FAKE_INIT % {"bindir": str(tmp_path / "bin")})
    monkeypatch.setattr(manager, "MODULE_INIT_SCRIPTS", (str(tmp_path / "missing.sh"),))
    monkeypatch.setenv("LMOD_PKG", str(pkg))

    env = wizard.capture_module_environment("module load good")

    assert env["FAKE_MODULE_LOADED"] == "1"


def test_failing_module_line_raises_with_command_and_shell_error(fake_init):
    with pytest.raises(wizard.ModuleCaptureError) as excinfo:
        wizard.capture_module_environment("module load nonexistent")

    message = str(excinfo.value)
    assert "module load nonexistent" in message
    assert "unknown module nonexistent" in message


def test_no_init_script_and_no_module_command_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "MODULE_INIT_SCRIPTS", (str(tmp_path / "missing.sh"),))
    monkeypatch.delenv("LMOD_PKG", raising=False)

    with pytest.raises(wizard.ModuleCaptureError) as excinfo:
        wizard.capture_module_environment("crab-no-such-command-xyz load gcc")

    assert "crab-no-such-command-xyz load gcc" in str(excinfo.value)
    assert "crab-no-such-command-xyz:" in str(excinfo.value)  # bash names it; wording is localised


def test_empty_command_returns_a_copy_of_the_environment(fake_init, monkeypatch):
    monkeypatch.setenv("CRAB_TEST_MARKER", "x")

    env = wizard.capture_module_environment("")

    assert env == dict(os.environ)
    assert env is not os.environ


def test_multiline_values_survive_the_capture(fake_init):
    env = wizard.capture_module_environment("module load good && export MULTI=$'a=b\\nc'")

    assert env["MULTI"] == "a=b\nc"


# ── wizard flow ───────────────────────────────────────────────────────────────


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class _FakeRecipe(BenchmarkRecipe):
    def __init__(self, name):
        self._name = name
        self.built_with_env = []

    @property
    def name(self):
        return self._name

    @property
    def benchmark_id(self):
        return self._name.lower()

    @property
    def build_manifest(self):
        return BuildManifest(requires_modules=True)

    def check_dependencies(self, env):
        return True, ""

    def download_and_build(self, target_dir, params, env, log_callback=None):
        self.built_with_env.append(env)
        return True, BuildResult(binary_path=os.path.join(target_dir, "bin")), ""

    def verify_existing(self, path):
        return False


def _drive(monkeypatch, tmp_path, recipes, module_lines):
    """Run option 4 for each recipe with the given module lines; return (saved, printed)."""
    saved = {}
    printed = []
    answers = []
    for line in module_lines:
        answers.extend(["4", line])
    queue = list(answers)

    def fake_ask(prompt, *args, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(wizard, "BENCHMARKS_DIR", str(tmp_path / "benchmarks"))
    monkeypatch.setattr(wizard.Prompt, "ask", fake_ask)
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(
        wizard.questionary, "checkbox", lambda *a, **k: _Answer([r.suite for r in recipes])
    )
    monkeypatch.setattr(wizard.memory, "get_receipt", lambda _id: None)
    monkeypatch.setattr(wizard.memory, "save_receipt", lambda _id, r: saved.__setitem__(_id, r))
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: "")
    monkeypatch.setattr(
        wizard.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))
    )

    groups = {r.suite: [r] for r in recipes}
    wizard._run_recipe_wizard(recipes, groups, [r.benchmark_id for r in recipes])
    assert queue == [], "the wizard asked fewer questions than the test scripted"
    return saved, "\n".join(printed)


def test_failing_module_line_skips_the_recipe_and_saves_nothing(fake_init, monkeypatch, tmp_path):
    first = _FakeRecipe("Alpha")
    second = _FakeRecipe("Beta")

    saved, printed = _drive(
        monkeypatch, tmp_path, [first, second], ["module load bad", "module load good"]
    )

    assert first.built_with_env == []
    assert "alpha" not in saved
    assert "module load bad" in printed
    assert "unknown module bad" in printed
    assert "Could not load the modules" in printed
    assert "bash -c 'module load bad'" in printed
    # the wizard went on to the next recipe
    assert len(second.built_with_env) == 1
    assert "beta" in saved


def test_working_module_line_builds_with_the_captured_env_and_saves_the_hook(
    fake_init, monkeypatch, tmp_path
):
    recipe = _FakeRecipe("Alpha")

    saved, _ = _drive(monkeypatch, tmp_path, [recipe], ["module load good"])

    assert recipe.built_with_env[0]["FAKE_MODULE_LOADED"] == "1"
    assert saved["alpha"]["type"] == "source"
    assert saved["alpha"]["hooks"]["pre_run"] == ["module load good"]


# ── the capture does not depend on the current folder ─────────────────────────


def test_capture_runs_bash_in_the_neutral_folder(fake_init, monkeypatch):
    calls = []
    real_run = wizard.subprocess.run

    def spy(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(wizard.subprocess, "run", spy)

    wizard.capture_module_environment("module load good")

    assert [cmd[:2] for cmd, _ in calls] == [["bash", "-c"]]
    assert calls[0][1]["cwd"] == wizard.paths.neutral_dir()


def test_capture_works_when_the_current_folder_was_deleted(fake_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # first, so the teardown restores the real current folder
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()

    env = wizard.capture_module_environment("module load good && export DEMO_CAPTURE=1")
    assert env["DEMO_CAPTURE"] == "1"

    with pytest.raises(wizard.ModuleCaptureError) as excinfo:
        wizard.capture_module_environment("echo oops >&2; false")
    assert "oops" in str(excinfo.value)
    assert "getcwd" not in str(excinfo.value)
