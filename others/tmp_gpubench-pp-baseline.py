import os
import sys
sys.path.append(os.path.dirname(__file__))
from crab.wrappers.base import sizeof_fmt
from gpubench_common import gpubench

class app(gpubench):
    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + "/src/microbench-gpu/bin/pp_Baseline"

    def get_bench_name(self):
        return "gpubench p2p Baseline"
