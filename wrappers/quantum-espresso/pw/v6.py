import os
import sys
sys.path.append(os.path.dirname(__file__))
from _pw_base import pw_base


class app(pw_base):

    @property
    def benchmark_id(self) -> str:
        return "qe-v6"
