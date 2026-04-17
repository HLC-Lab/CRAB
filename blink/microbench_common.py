import sys
import os
from crab.wrappers.base import base, sizeof_fmt

class microbench(base):
    metadata = [
        {'name': 'Avg-Duration'     , 'unit': 's', 'conv': True }, 
        {'name': 'Min-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Max-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Median-Duration'  , 'unit': 's', 'conv': False},
        {'name': 'MainRank-Duration', 'unit': 's', 'conv': False}
    ]

    def get_path(self, name):
        # 2. Use the path dynamically injected by the Orchestrator/Paths memory
        blink_dir = os.environ.get("CRAB_BLINK_PATH", "")
        return os.path.join(blink_dir, name)

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
