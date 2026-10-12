"""Fixture wrapper for CRAB's own tests (not a shipped wrapper).

The simplest valid wrapper: it looks its binary up on PATH and parses one number per word of
stdout. Tests that need "some wrapper on the search path" point CRAB_PATH_WRAPPERS at
tests/fixtures/wrappers and expect this one by its relpath, `fixture/path_app.py`.
"""

from crab.wrappers.base import base


class app(base):
    executable = "echo"
    metadata = [{"name": "t", "unit": "s", "conv": True}]

    def read_data(self):
        return [{"t": float(x)} for x in self.stdout.split()]
