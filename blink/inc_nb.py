import sys
import os
sys.path.append(os.path.dirname(__file__))
from microbench_common import microbench

class app(microbench):
    def get_binary_path(self):
        return self.get_path("inc_nb")
    
    def get_bench_name(self):
        return "Incast (non-blocking)"