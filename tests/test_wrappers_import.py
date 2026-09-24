"""Every shipped wrapper must load the way the engine loads it and expose `class app`.

Loads each file with the same `spec_from_file_location` call as
`core/experiment/runner.py`'s `load_module`, so a wrapper that crashes at import time
(e.g. a missing import) fails here instead of on a cluster. Parsing correctness is the job
of the golden-fixture suite (roadmap M1); this only proves each wrapper can be loaded.
"""

import importlib.util
from pathlib import Path

import pytest

_WRAPPERS = Path(__file__).resolve().parents[1] / "wrappers"
_HELPERS = {"__init__.py"}  # plus *_common.py / *_base.py: shared code, no `class app`

# Known broken at import time; fixed in roadmap M1 (measurement trustworthiness).
# strict=True: when M1 fixes one, this list must shrink or the suite goes red.
_KNOWN_BROKEN: dict[str, str] = {
    "others/amg.py": "subclasses the base wrapper without importing it (NameError)",
}


def _wrapper_files() -> list[Path]:
    return sorted(
        p
        for p in _WRAPPERS.rglob("*.py")
        if "__pycache__" not in p.parts
        and p.name not in _HELPERS
        and not p.stem.endswith(("_common", "_base"))
    )


def _params():
    for path in _wrapper_files():
        rel = str(path.relative_to(_WRAPPERS))
        marks = []
        if rel in _KNOWN_BROKEN:
            marks.append(pytest.mark.xfail(reason=_KNOWN_BROKEN[rel], strict=True))
        yield pytest.param(path, id=rel, marks=marks)


def test_wrapper_tree_is_not_empty():
    assert len(_wrapper_files()) > 50


@pytest.mark.parametrize("path", _params())
def test_wrapper_loads_and_defines_app(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert isinstance(getattr(mod, "app", None), type)
