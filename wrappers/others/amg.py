import sys
import os
sys.path.append(os.path.dirname(__file__))

from crab.wrappers.base import base


class app(base):  
    metadata = [
        {'name': 'spatial_operator', 'unit': 's', 'conv': False},
        {'name': 'IJ_vector_setup' , 'unit': 's', 'conv': False},
        {'name': 'AMG_setup'       , 'unit': 's', 'conv': False},
        {'name': 'AMG_solve'       , 'unit': 's', 'conv': False},
        {'name': 'total'           , 'unit': 's', 'conv': True}
    ]

    def get_binary_path(self):
        env_name = "CRAB_PATH_AMG"
        if env_name not in os.environ or os.environ[env_name] == "":
            return None
        else:
            return os.environ[env_name]

    def read_data(self):  # return list (size num_metrics) of variable size lists
        lines = self.stdout.split('\n')
        if len(lines) < 45:
            raise ValueError(f"AMG output has {len(lines)} lines, expected at least 45 (the run did not finish?)")
        lines = lines[11], lines[22], lines[31], lines[44]
        data = [float(x.split(' ')[-2]) for x in lines]
        data += [sum(data)]
        data = [[x] for x in data]
        return data

    def get_bench_name(self):
        return "AMG"
    
    def get_bench_input(self):
        return ""
