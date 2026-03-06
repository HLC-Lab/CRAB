import sys
import os
sys.path.append(os.environ["CRAB_ROOT"] + "/wrappers")
from microbench_common import microbench

class app(microbench):
    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + "/benchmarks/workerpool/workerpool_scheduler.py"
    
    def get_bench_name(self):
        return "Workerpool Scheduler"
