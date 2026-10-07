import sys
import os
import re
from crab.wrappers.base import base, sizeof_fmt

class microbench(base):
    @property
    def benchmark_id(self) -> str:
        return "blink"

    metadata = [
        {'name': 'Avg-Duration'     , 'unit': 's', 'conv': True }, 
        {'name': 'Min-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Max-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Median-Duration'  , 'unit': 's', 'conv': False},
        {'name': 'MainRank-Duration', 'unit': 's', 'conv': False}
    ]

    def get_path(self, name):
        # A source or binary receipt holds blink's bin/ directory. A module receipt holds the
        # executable the user typed: a command name means every microbench is on PATH once the
        # module loads; a path means the microbenches sit in that file's directory.
        receipt = self.get_receipt()
        if not receipt:
            return None
        stored = receipt.get("binary_path", "")
        if not stored:
            return None
        if receipt.get("type", "source") == "module":
            if not os.path.dirname(stored):
                return name
            if not os.path.isdir(stored):
                return os.path.join(os.path.dirname(stored), name)
        return os.path.join(stored, name)

    # The CSV header the benchmark prints before its samples (common.h write_results). Its
    # columns map, in order, to the metrics in `metadata`.
    table_header = "Average,Minimum,Maximum,Median,MainRank"

    def read_data(self):
        # The table is found by its header, not by line position: MPI/UCX warnings and extra
        # preamble lines (pw-ping-pong_b prints its targets) can come before it. Lines in the
        # table that don't start with a digit are such warnings and are skipped; the trailer's
        # sample count catches anything lost.
        lines = [line.strip() for line in self.stdout.splitlines()]
        if self.table_header not in lines:
            raise ValueError(f"no '{self.table_header}' header line in the output")
        names = [m['name'] for m in self.metadata]
        rows = []
        measured = None
        for line in lines[lines.index(self.table_header) + 1:]:
            match = re.match(r"Ran \d+ iterations\. Measured (\d+) iterations\.$", line)
            if match:
                measured = int(match.group(1))
                break
            if not line[:1].isdigit():
                continue
            values = line.split(',')
            if len(values) != len(names):
                raise ValueError(f"expected {len(names)} comma-separated numbers, got {line!r}")
            rows.append(dict(zip(names, (float(v) for v in values))))
        if measured is None:
            raise ValueError("output ends before the 'Ran N iterations. Measured M iterations.' line (the run did not finish?)")
        if len(rows) != measured:
            raise ValueError(f"table has {len(rows)} rows but the benchmark measured {measured} iterations")
        return rows

    def get_bench_input(self):
        if "-msgsize" not in self.args:
            return ""
        else:
            args_values = self.args.split(" ") 
            size_bytes = args_values[args_values.index('-msgsize') + 1]
            return sizeof_fmt(int(size_bytes))
