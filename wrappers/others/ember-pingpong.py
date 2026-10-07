import os
import sys
sys.path.append(os.path.dirname(__file__))
from crab.wrappers.base import base
from _ember_common import read_result_row

class app(base):
    metadata = [
        {'name': 'MsgSize'       , 'unit': 'B'     , 'conv': False},
        {'name': 'Time'          , 'unit': 's'     , 'conv': True },
        {'name': 'Msgs'          , 'unit': 'KMsgs' , 'conv': False},
        {'name': 'Bytes'         , 'unit': 'MB'    , 'conv': False},
        {'name': 'Msg-Throughput', 'unit': 'KMsg/s', 'conv': False},
        {'name': 'Throughput'    , 'unit': 'MB/s'  , 'conv': False},
    ]

    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + '/src/ember/mpi/pingpong/pingpong'

    def read_data(self):
        return read_result_row(self.stdout, self.metadata)
    
    def get_bench_name(self):
        return "Ember - PingPong"
    
    def get_bench_input(self):
        return ""