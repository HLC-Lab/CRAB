import os

# Clean import directly from the installed framework
from crab.wrappers.base import base

class app(base):
    exists = True

    metadata = [
        {'name': 'graph_generation'   , 'unit': 's', 'conv': True},
        {'name': 'construction'       , 'unit': 's', 'conv': True},
        {'name': 'redistribution_time', 'unit': 's', 'conv': True},
        {'name': 'min_time'           , 'unit': 's', 'conv': True},
        {'name': 'q1_time'            , 'unit': 's', 'conv': True},
        {'name': 'median_time'        , 'unit': 's', 'conv': True},
        {'name': 'q3_time'            , 'unit': 's', 'conv': True},
        {'name': 'max_time'           , 'unit': 's', 'conv': True},
        {'name': 'mean_time'          , 'unit': 's', 'conv': True},
        {'name': 'stddev_time'        , 'unit': 's', 'conv': True},
        {'name': 'min_valid'          , 'unit': 's', 'conv': True},
        {'name': 'q1_valid'           , 'unit': 's', 'conv': True},
        {'name': 'median_valid'       , 'unit': 's', 'conv': True},
        {'name': 'q3_valid'           , 'unit': 's', 'conv': True},
        {'name': 'max_valid'          , 'unit': 's', 'conv': True},
        {'name': 'mean_valid'         , 'unit': 's', 'conv': True},
        {'name': 'stddev_valid'       , 'unit': 's', 'conv': True},
    ]

    def get_binary_path(self):
        env_name = "CRAB_PATH_G500"
        if env_name not in os.environ or os.environ[env_name] == "":
            self.exists = False
            return None
        else:
            # Join the injected directory with the specific executable name
            return os.path.join(os.environ[env_name], "graph500_reference_bfs")

    def read_data(self):
        if self.exists:
            output = self.stdout
            lines = output.split('\n')
            lines = [x for x in lines if x.strip() != '']
            lines = lines[4:15]+lines[-7:]
            lines = ([lines[0]]+lines[2:])
            data = [[float(x.split(' ')[-1])] for x in lines]
            return data
        else:
            # Fallback if binary isn't found
            return [[0] * len(self.metadata)]

    def get_bench_name(self):
        return "Graph500"
    
    def get_bench_input(self):
        return ""
