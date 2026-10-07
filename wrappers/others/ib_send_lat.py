import os
import shlex
from crab.wrappers.base import base, sizeof_fmt

# First TCP port for the per-device connections; device i uses FIRST_PORT + i.
FIRST_PORT = 18515


def ib_devices():
    """The InfiniBand devices to measure, from CRAB_IB_DEVICES (e.g. "mlx5_0#mlx5_1")."""
    devices = [d for d in os.environ.get("CRAB_IB_DEVICES", "").split("#") if d]
    if not devices:
        raise RuntimeError("CRAB_IB_DEVICES must name the IB devices to run ib_send_lat on, e.g. mlx5_0#mlx5_1")
    return devices


class app(base):
    # perftest's latency test, looked up on PATH unless the config gives a 'binary'.
    executable = "ib_send_lat"

    metadata = [
        {'name': 'time' , 'unit': 'us', 'conv': True},
    ]

    def run_app(self):
        # One ib_send_lat per device, all at once, each on its own port and writing every
        # sample (-U) to ib_send_lat<i> in the working directory (this run's run_dir).
        binary = shlex.quote(self.resolve_binary())
        launches = [
            f"({binary} --perform_warm_up --ib-dev={shlex.quote(dev)} --report_gbits -t 1 -F -U"
            f" -p {FIRST_PORT + i} {self.args} > ib_send_lat{i} 2>&1) &"
            for i, dev in enumerate(ib_devices())
        ]
        return "bash -c " + shlex.quote(" ".join(launches) + " wait")

    def read_data(self):  # return list (size num_metrics) of variable size lists
        files = [os.path.join(self.run_dir, f"ib_send_lat{i}") for i in range(len(ib_devices()))]

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
