"""Fixture wrapper for CRAB's own tests (not a shipped wrapper).

Like the Quantum ESPRESSO wrappers it takes its binary from a setup receipt (`benchmark_id`) and
has no `executable` to find on PATH, so without that receipt `setup()` fails with
MissingBinaryError. It builds its own command (overrides `run_app`), so the pre-flight check at
setup is what catches the missing install.
"""

from crab.wrappers.base import base


class app(base):
    benchmark_id = "fixture-receipt-app"
    metadata = [{"name": "t", "unit": "s", "conv": True}]

    def run_app(self):
        return f"{self.resolve_binary()} {self.args}"

    def read_data(self):
        return [{"t": float(x)} for x in self.stdout.split()]
