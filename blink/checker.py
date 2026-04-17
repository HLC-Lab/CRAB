import sys
import os
sys.path.append(os.path.dirname(__file__))
from microbench_common import microbench

class app(microbench):
    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + "/src/microbench/bin/checker" 
    
    def get_bench_name(self):
        return "Checker"