"""Tripwire: no code under `src/crab` hands the current folder to a call.

A `crab` command must answer the same from any folder (plan 112). The bug that started it passed
`"."` positionally to a subprocess helper (`self.run_command_streamed([...], ".", ...)`), so a
`cwd="."` pattern would have missed it. This check parses every module with `ast` and looks at
positional and keyword arguments alike.

Flagged:
  1. a call to `os.getcwd`, `os.getcwdb`, `Path.cwd`, `pathlib.Path.cwd`, `os.chdir`, `os.fchdir`;
  2. any other call with an argument (also inside a list or tuple argument) that is the string
     `"."`, a string starting with `"./"` or `"../"`, or `".env"`. Calls to string methods that
     take such literals on purpose, to `re.*`, to logger methods and to `print` are skipped.

`ALLOWED` holds the examined exceptions, keyed by (file under `src/crab`, enclosing function).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "crab"

# The built frontend ships inside the package; it holds no Python of CRAB's own.
SKIPPED_FOLDERS = ("webui", "web/static")

# (file, enclosing function) -> why the current folder is intended there. Add an entry only for a
# path the user passed for that one command; a bug gets fixed, never listed.
ALLOWED: dict[tuple[str, str], str] = {
    ("fsutil.py", "leave_dir_before_remove"): (
        "the helper that steps out of a folder it is about to remove"
    ),
    ("core/provenance.py", "build_provenance"): (
        'the "." of `git -C <wrappers dir> status -- .` is a pathspec git resolves against '
        "the absolute `-C` folder, never against the current folder"
    ),
}

_CWD_CALLS = {
    "os.getcwd",
    "os.getcwdb",
    "os.chdir",
    "os.fchdir",
    "Path.cwd",
    "pathlib.Path.cwd",
}

# String and bytes methods that legitimately take "." or "./" literals.
_STRING_METHODS = {
    "split",
    "rsplit",
    "partition",
    "rpartition",
    "startswith",
    "endswith",
    "replace",
    "join",
    "strip",
    "lstrip",
    "rstrip",
    "removeprefix",
    "removesuffix",
    "count",
    "find",
    "format",
    "lower",
    "encode",
}

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` for a chain of names and attributes, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_exempt_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if isinstance(func, ast.Attribute):
        if func.attr in _STRING_METHODS or func.attr in _LOG_METHODS:
            return True
        dotted = _dotted(func)
        return dotted is not None and dotted.split(".")[0] in ("re", "logging")
    return False


def _is_cwd_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
        return text == "." or text == ".env" or text.startswith(("./", "../"))
    if isinstance(node, ast.JoinedStr) and node.values:
        # `f".{name}.tmp"` is a hidden name, not a path: only a "./" or "../" start counts.
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value.startswith(("./", "../"))
    return False


def _literal_arguments(node: ast.expr) -> list[ast.expr]:
    """`node` and, for a list, tuple or set, the elements inside it (recursively)."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        found: list[ast.expr] = []
        for element in node.elts:
            found.extend(_literal_arguments(element))
        return found
    return [node]


def _call_problem(call: ast.Call) -> tuple[int, str] | None:
    """(line, why) when `call` depends on the current folder, else None."""
    dotted = _dotted(call.func)
    if dotted in _CWD_CALLS:
        return call.lineno, f"{dotted} reads or changes the current folder"
    if isinstance(call.func, ast.Name) and call.func.id in ("getcwd", "chdir"):
        return call.lineno, f"{call.func.id} reads or changes the current folder"
    if _is_exempt_call(call):
        return None
    arguments = list(call.args) + [keyword.value for keyword in call.keywords]
    for argument in arguments:
        for item in _literal_arguments(argument):
            if _is_cwd_literal(item):
                return item.lineno, "an argument names the current folder"
    return None


class _Finder(ast.NodeVisitor):
    def __init__(self, rel: str, source_lines: list[str], allowed: dict[tuple[str, str], str]):
        self.rel = rel
        self.lines = source_lines
        self.allowed = allowed
        self.scope: list[str] = []
        self.hits: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        function = self.scope[-1] if self.scope else "<module>"
        if (self.rel, function) not in self.allowed:
            problem = _call_problem(node)
            if problem:
                number, why = problem
                line = self.lines[number - 1].strip()
                self.hits.append(f"src/crab/{self.rel}:{number}: {line}  [{why}]")
        self.generic_visit(node)


def find_cwd_uses(
    source: str, rel: str = "snippet.py", allowed: dict[tuple[str, str], str] | None = None
) -> list[str]:
    """The `file:line: <source line>` hits in `source`, a module whose path under `src/crab` is `rel`."""
    finder = _Finder(rel, source.splitlines(), ALLOWED if allowed is None else allowed)
    finder.visit(ast.parse(source))
    return finder.hits


def _scan_tree() -> list[str]:
    hits: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(tuple(folder + "/" for folder in SKIPPED_FOLDERS)):
            continue
        hits.extend(find_cwd_uses(path.read_text(), rel))
    return hits


def test_no_code_passes_the_current_folder_to_a_call():
    hits = _scan_tree()
    assert not hits, (
        "a command must not depend on the current folder: pass an absolute path "
        "(or, for a path the user typed, resolve it once at the CLI edge):\n" + "\n".join(hits)
    )


def test_every_allowlist_entry_names_a_real_function():
    for rel, function in ALLOWED:
        source = (SRC / rel).read_text()
        names = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function in names, f"{rel}: no function {function}"


# --------------------------------------------------------------------------- #
# the checker itself, on source strings
# --------------------------------------------------------------------------- #
def test_the_original_positional_dot_is_flagged():
    source = (
        "class Recipe:\n"
        "    def clone(self, url, target_dir, env, cb):\n"
        '        self.run_command_streamed(["git", "clone", url, target_dir], ".", "Cloning...", env, cb)\n'
    )
    assert len(find_cwd_uses(source)) == 1


def test_a_keyword_dot_is_flagged():
    assert len(find_cwd_uses('import subprocess\nsubprocess.run(cmd, cwd=".")\n')) == 1


def test_a_dot_inside_a_list_argument_is_flagged():
    assert len(find_cwd_uses('run(["ls", "./data"])\n')) == 1


def test_an_env_file_in_the_current_folder_is_flagged():
    assert len(find_cwd_uses('open(".env")\n')) == 1


def test_a_current_folder_read_is_flagged():
    assert len(find_cwd_uses("import os\nos.getcwd()\n")) == 1
    assert len(find_cwd_uses("from pathlib import Path\nPath.cwd()\n")) == 1
    assert len(find_cwd_uses("import os\nos.chdir(folder)\n")) == 1


def test_a_call_with_a_folder_variable_is_not_flagged():
    assert find_cwd_uses("import subprocess\nsubprocess.run(cmd, cwd=folder)\n") == []


def test_string_methods_and_joins_under_a_root_are_not_flagged():
    assert find_cwd_uses('name.split(".")\n') == []
    assert find_cwd_uses('import os\nos.path.join(root, "data")\n') == []
    assert find_cwd_uses('name.startswith("./")\n') == []
    assert find_cwd_uses('import re\nre.sub(r"\\.", "", text)\nprint(".")\n') == []


def test_a_call_under_an_allowlisted_function_is_skipped():
    source = "import os\ndef step_out(folder):\n    os.chdir('.')\n"
    allowed = {("snippet.py", "step_out"): "test"}
    assert find_cwd_uses(source, allowed=allowed) == []
    assert len(find_cwd_uses(source, allowed={})) == 1
