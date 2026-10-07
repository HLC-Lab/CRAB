import sys
import os
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

    def read_data(self):
        out_string = self.stdout
        tmp_list = []

        for line in out_string.splitlines()[2:-1]:
            tmp_list += [[float(x) for x in line.split(',')]]
        data_list = [list(x) for x in zip(*tmp_list)]
        return data_list

    def get_bench_input(self):
        if "-msgsize" not in self.args:
            return ""
        else:
            args_values = self.args.split(" ") 
            size_bytes = args_values[args_values.index('-msgsize') + 1]
            return sizeof_fmt(int(size_bytes))
