import glob
import os
from crab.wrappers.base import base

class app(base):
    metadata = [
        {'name': 'matrix_structure', 'unit': 's', 'conv': False},
        {'name': 'FE_assambly'     , 'unit': 's', 'conv': False},
        {'name': 'WAXPY'           , 'unit': 's', 'conv': False},
        {'name': 'DOT'             , 'unit': 's', 'conv': False},
        {'name': 'MATVEC'          , 'unit': 's', 'conv': False},
        {'name': 'CG_total'        , 'unit': 's', 'conv': False},
        {'name': 'CG_per_iteration', 'unit': 's', 'conv': False},
        {'name': 'total'           , 'unit': 's', 'conv': True}
    ]

    def get_binary_path(self):
        env_name = "CRAB_PATH_MINIFE"
        if env_name not in os.environ or os.environ[env_name] == "":
            return None
        else:
            return os.environ[env_name]

    def read_data(self):  # return list (size num_metrics) of variable size lists
        # miniFE writes its report, miniFE.<size>.P<procs>.<date>.yaml, into its working
        # directory, which is this run's run_dir (the runner cleans it up).
        reports = sorted(glob.glob(os.path.join(glob.escape(self.run_dir), "miniFE*.yaml")))
        if not reports:
            raise ValueError("no miniFE*.yaml report in the run directory (the run did not finish?)")
        if len(reports) > 1:
            names = ", ".join(os.path.basename(r) for r in reports)
            raise ValueError(f"several miniFE reports in the run directory, expected one: {names}")
        with open(reports[0], 'r') as file:
            lines = file.readlines()
        if len(lines) < 62:
            raise ValueError(f"{os.path.basename(reports[0])} has {len(lines)} lines, expected at least 62")
        idxs = [28, 30, 45, 48, 51, 55, 58, 61]
        return [[float(lines[idx].split(' ')[-1])] for idx in idxs]

    def get_bench_name(self):
        return "MiniFE"
    
    def get_bench_input(self):
        return ""
