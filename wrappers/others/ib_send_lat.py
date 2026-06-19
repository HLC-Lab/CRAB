import sys
import os
sys.path.append(os.path.dirname(__file__))
from base import base,sizeof_fmt
import ast

class app(base):
    metadata = [
        {'name': 'time' , 'unit': 'us', 'conv': True},
    ]

    def get_binary_path(self):
        return os.environ["CRAB_ROOT"] + "/wrappers/ib_send_lat.sh"

    def read_data(self):  # return list (size num_metrics) of variable size lists
        ib_devices_env = os.environ.get("CRAB_IB_DEVICES")
        if not ib_devices_env:
            raise RuntimeError("CRAB_IB_DEVICES must be set to run ib_send_lat")
        ib_devices = ib_devices_env.count("#") + 1
        files = [f"ib_send_lat{i}" for i in range(ib_devices)]

        samples = []
        for path in files:
            start = False
            i = 0
            warmup = 10
            with open(path) as file:
                lines = file.readlines()
                samples_rank = []
                for line in lines:
                    line_clean = line.strip()
                    if line_clean == "#, usec":
                        start = True
                        continue
                    if start and not line_clean.startswith("---"):
                        if i >= warmup:
                            time = line_clean.split(",")[1].strip()
                            samples_rank += [float(time)]
                        i += 1
                    else:
                        start = False
            samples += [samples_rank]

        # Use zip(*samples) to avoid IndexError when devices have unequal sample counts
        samples_max = [max(row) for row in zip(*samples)]
        return [samples_max]

    def get_bench_name(self):
        return "ib_send_lat"
    
    def get_bench_input(self):
        args_fields = self.args.split(" ")
        pos = args_fields.index("-s") + 1
        return sizeof_fmt(int(args_fields[pos]))
