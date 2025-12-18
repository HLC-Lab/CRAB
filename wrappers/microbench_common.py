import sys
import os
import re
sys.path.append(os.environ["CRAB_ROOT"] + "/wrappers")
from base import base,sizeof_fmt

class microbench(base):
    metadata = [
        {'name': 'Avg-Duration'     , 'unit': 's', 'conv': True }, # Per ogni iterazione da la media tra tutti i Rank
        {'name': 'Min-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Max-Duration'     , 'unit': 's', 'conv': False},
        {'name': 'Median-Duration'  , 'unit': 's', 'conv': False},
        {'name': 'MainRank-Duration', 'unit': 's', 'conv': False}
    ]

    def get_path(self, name):
        p = ""
        # sys = os.environ["CRAB_SYSTEM"]
        # Da ignorare, faremo su leonardo
        # if sys == "leonardo":
        #     p += os.environ["CRAB_ROOT"] + "/src/microbench/select_nic_ucx "
        p += os.environ["CRAB_ROOT"] + "/benchmarks/blink/bin/" + name
        return p

    def read_data(self):
        out_string = self.stdout
        tmp_list = []

        print(out_string)
        #print(out_string.splitlines()[-1])
        for line in out_string.splitlines()[2:-1]:
            row = []
            for x in line.split(','):
                s = x.strip()
                try:
                    row.append(float(s))
                except (ValueError, TypeError):
                    continue
            tmp_list.append(row)

        max_len = max((len(r) for r in tmp_list), default=0)
        for r in tmp_list:
            r.extend([None] * (max_len - len(r)))

        data_list = [list(col) for col in zip(*tmp_list)] if tmp_list else []
        return data_list


        
   

    def read_data(self):
        _num = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
        _num_csv_5 = re.compile(rf"^\s*{_num}\s*,\s*{_num}\s*,\s*{_num}\s*,\s*{_num}\s*,\s*{_num}\s*$")
        out_string = self.stdout
        tmp_list = []

        for line in out_string.splitlines()[2:]:
            line = line.strip()
            if not line:
                continue
            # Keep only comma-separated numeric rows
            if not _num_csv_5.match(line):
                continue

            tmp_list += [[float(x) for x in line.split(',')]]

        data_list = [list(x) for x in zip(*tmp_list)]
        return data_list
        
    def read_error(self):
        return str(self.stderr)

    def get_bench_input(self):
        if "-msgsize" not in self.args:
            return ""
        else:
            args_values = self.args.split(" ") 
            size_bytes = args_values[args_values.index('-msgsize') + 1]
            return sizeof_fmt(int(size_bytes))
