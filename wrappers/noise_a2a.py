import sys
import os
sys.path.append(os.environ["CRAB_ROOT"] + "/wrappers")
from microbench_common import microbench

class app(microbench):
    def get_binary_path(self):
        return self.get_path("noise_a2a")
    
    def get_bench_name(self):
        return "Noise All-to-all"
