import os
import sys
sys.path.append(os.path.dirname(__file__))
from _ph_base import ph_base


class app(ph_base):

    @property
    def benchmark_id(self) -> str:
        return "qe-v6"
