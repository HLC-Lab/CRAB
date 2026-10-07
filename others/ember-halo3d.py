import os
import sys
sys.path.append(os.path.dirname(__file__))
from crab.wrappers.base import base
from _ember_common import read_result_row

class app(base):
    metadata = [
        {'name': 'Time'                 , 'unit': 's'        , 'conv': True },
        {'name': 'MaxData_xchanged/rank', 'unit': 'KB/rank'  , 'conv': False},
        {'name': 'Throughput/rank'      , 'unit': 'MB/s/Rank', 'conv': False}
    ]

    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + "/src/ember/mpi/halo3d/halo3d"
        
    def read_data(self):
        return read_result_row(self.stdout, self.metadata)
        
    def get_bench_name(self):
        return "Ember - Halo3D"
    
    def get_bench_input(self):
        return ""