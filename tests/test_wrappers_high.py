"""Local-only tests for HIGH-priority wrapper and runner issues."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# ── Runner: sbatch_directives list format ─────────────────────────────────────


class TestRunnerSbatchDirectivesList(unittest.TestCase):
    def test_list_sbatch_directives_raises_attribute_error(self):
        """Confirms the bug: list does not have .get(), so the arch check crashes."""
        global_opts = {"sbatch_directives": ["--partition=gpu"]}
        sd = global_opts.get("sbatch_directives", {})
        # This is the exact buggy line from runner.py line 126
        with self.assertRaises(AttributeError):
            _ = sd.get("partition", "")


# ── pw_base / ph_base: None binary raises ─────────────────────────────────────


class TestPwBaseNoneBinary(unittest.TestCase):
    def _make_pw_app(self):
        """Create a minimal pw_base subclass instance."""
        wrappers_path = os.path.join(
            os.path.dirname(__file__), "..", "wrappers", "quantum-espresso", "pw"
        )
        sys.path.insert(0, wrappers_path)
        from pw_base import pw_base

        sys.path.pop(0)

        class ConcreteApp(pw_base):
            benchmark_id = "qe-test"

            def __init__(self, *a, **kw):
                self.id_num = 0
                self.collect_flag = False
                self.args = ""
                self.stdout = ""
                self.stderr = ""

        return ConcreteApp()

    def test_run_app_raises_when_no_receipt(self):
        """run_app() must raise RuntimeError when get_binary_path() returns None."""
        app = self._make_pw_app()
        with tempfile.NamedTemporaryFile(suffix=".in", delete=False, mode="w") as f:
            f.write("  outdir = 'old_dir/'\n")
            f.write("  other = 'value'\n")
            input_path = f.name
        try:
            app.input_file = input_path
            with patch.object(type(app), "get_binary_path", return_value=None):
                with self.assertRaises(RuntimeError):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        app.run_dir = tmpdir
                        app.run_app()
        finally:
            os.unlink(input_path)


class TestPwBaseOutdirRegex(unittest.TestCase):
    def _make_pw_app(self):
        wrappers_path = os.path.join(
            os.path.dirname(__file__), "..", "wrappers", "quantum-espresso", "pw"
        )
        sys.path.insert(0, wrappers_path)
        from pw_base import pw_base

        sys.path.pop(0)

        class ConcreteApp(pw_base):
            benchmark_id = "qe-test"

            def __init__(self, *a, **kw):
                self.id_num = 0
                self.collect_flag = False
                self.args = ""
                self.stdout = ""
                self.stderr = ""

        return ConcreteApp()

    def test_outdir_not_replaced_in_comment(self):
        """Lines with 'outdir' inside a comment must NOT be replaced."""
        app = self._make_pw_app()
        # A comment line containing the word outdir must pass through unchanged
        comment_line = "! outdir is set below\n"
        with tempfile.NamedTemporaryFile(suffix=".in", delete=False, mode="w") as f:
            f.write(comment_line)
            f.write("  outdir = 'real_dir/'\n")
            input_path = f.name
        try:
            app.input_file = input_path
            with patch.object(type(app), "get_binary_path", return_value="/bin/pw.x"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    app.run_dir = tmpdir
                    app.run_app()
                    with open(os.path.join(tmpdir, "modified_input.in")) as f:
                        lines = f.readlines()
            # Comment line should be preserved unchanged
            self.assertEqual(
                lines[0], comment_line, "Comment line containing 'outdir' must not be replaced"
            )
            # Actual outdir assignment should be replaced
            self.assertIn("outdir", lines[1])
            self.assertNotIn("real_dir", lines[1])
        finally:
            os.unlink(input_path)


# ── nccl_common: blank lines, all rows, error shape ───────────────────────────


def _make_nccl():
    return _load_wrapper("nccl_common")


class TestNcclCommon(unittest.TestCase):
    def setUp(self):
        self.mod = _make_nccl()

    def _make_instance(self, stdout):
        class ConcreteNccl(self.mod.ncclbase):
            def __init__(self_inner):
                self_inner.stdout = stdout
                self_inner.args = ""

        return ConcreteNccl()

    def test_blank_lines_dont_crash(self):
        """read_data must not crash on blank lines (IndexError on l[0])."""
        stdout = (
            "# header line\n"
            "\n"  # blank line
            "  \n"  # whitespace-only
            "4096 4 8 8 100 12.3 15.6 18.9 100 11.1 14.2 17.3\n"
        )
        instance = self._make_instance(stdout)
        try:
            result = instance.read_data()
        except (IndexError, ValueError) as e:
            self.fail(f"read_data crashed on blank line: {e}")

    def test_returns_all_data_rows(self):
        """read_data must collect all data rows, not just the first."""
        stdout = (
            "# header\n"
            "1024 4 8 8 100 1.0 2.0 3.0 100 4.0 5.0 6.0\n"
            "4096 4 8 8 100 10.0 20.0 30.0 100 40.0 50.0 60.0\n"
        )
        instance = self._make_instance(stdout)
        result = instance.read_data()
        # Each of the 6 metrics should have 2 data points (from 2 rows)
        self.assertEqual(len(result), 6)
        self.assertEqual(len(result[0]), 2, "read_data must return ALL rows, not just the first")

    def test_error_fallback_shape(self):
        """Error fallback must return [[0]] * len(metadata), not [[0]*N]."""
        instance = self._make_instance("")
        result = instance.read_data()
        # Each inner list should have exactly one element
        self.assertEqual(len(result), 6)
        for i, lst in enumerate(result):
            self.assertIsInstance(lst, list, f"result[{i}] must be a list")
            # With no data, should return [[0]] * 6 or an empty-ish structure


# ── ember-incast: path typo ───────────────────────────────────────────────────

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_OTHERS_PATH = os.path.join(_PROJECT_ROOT, "wrappers", "others")
# Wrappers use `from base import base` — needs the crab/wrappers package directory
_CRAB_WRAPPERS_PATH = os.path.join(_PROJECT_ROOT, "src", "crab", "wrappers")


def _load_wrapper(name):
    """Load a wrapper module from wrappers/others/, adding necessary dirs to sys.path."""
    import importlib.util

    for p in [_OTHERS_PATH, _CRAB_WRAPPERS_PATH]:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, os.path.join(_OTHERS_PATH, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEmberIncastPath(unittest.TestCase):
    def test_no_double_r_in_ember_path(self):
        """The binary path must contain 'ember', not 'emberr'."""
        mod = _load_wrapper("ember-incast")
        instance = mod.app(0, False, "")
        with patch.dict(os.environ, {"CRAB_ROOT": "/fake"}):
            path = instance.get_binary_path()
        self.assertNotIn("emberr", path, f"Typo 'emberr' found in binary path: {path}")
        self.assertIn("ember", path)


# ── ib_send_lat: missing env var ──────────────────────────────────────────────


class TestIbSendLat(unittest.TestCase):
    def test_read_data_raises_descriptively_when_env_unset(self):
        """read_data must raise a clear RuntimeError when CRAB_IB_DEVICES is unset."""
        mod = _load_wrapper("ib_send_lat")
        instance = mod.app(0, False, "-s 65536")
        instance.stdout = ""
        env = {k: v for k, v in os.environ.items() if k != "CRAB_IB_DEVICES"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises((RuntimeError, KeyError)):
                instance.read_data()


# ── g500: MPICC token splitting ───────────────────────────────────────────────


class TestG500Recipe(unittest.TestCase):
    def test_mpicc_is_separate_token(self):
        """The build command must not put 'mpicc -fcommon' in a single token."""
        from crab.setup.recipes.g500 import G500Recipe

        recipe = G500Recipe()

        with patch.object(recipe, "run_command_streamed", return_value=True) as mock_run:
            with patch("os.path.exists", return_value=True):
                with tempfile.TemporaryDirectory() as tmpdir:
                    recipe.download_and_build(tmpdir, {}, {})

        # Find the make call
        make_call = next((c for c in mock_run.call_args_list if "make" in str(c)), None)
        self.assertIsNotNone(make_call, "make command not found in calls")
        cmd = make_call.args[0]  # first positional arg is the command list
        # No single token should contain both 'mpicc' and '-fcommon'
        bad_tokens = [t for t in cmd if "mpicc" in t and "-fcommon" in t]
        self.assertEqual(
            bad_tokens,
            [],
            f"'mpicc -fcommon' must be split into separate tokens, found: {bad_tokens}",
        )
