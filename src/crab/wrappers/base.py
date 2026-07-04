import crab.setup.memory as memory


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:.0f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:f}Yi{suffix}"


class base:
    def __init__(self, id_num, collect_flag, args):
        self.id_num = id_num
        self.args = args
        self.collect_flag = collect_flag
        self.node_list = []
        self.num_nodes = 0
        self.process = None

    @property
    def benchmark_id(self) -> str:
        """Override this in your wrapper to link to the setup receipt."""
        return ""

    def get_receipt(self):
        if not self.benchmark_id:
            return None
        return memory.get_receipt(self.benchmark_id)

    def get_pre_commands(self) -> list:
        receipt = self.get_receipt()
        if receipt and "hooks" in receipt:
            return receipt["hooks"].get("pre_run", [])
        return []

    def get_launcher_override(self) -> str:
        receipt = self.get_receipt()
        if receipt:
            return receipt.get("launcher_override", "")
        return ""

    def get_binary_path(self):
        receipt = self.get_receipt()
        if receipt:
            return receipt.get("binary_path")
        return None

    def set_process(self, process):
        self.process = process

    def set_output(self, stdout, stderr):
        self.stdout = stdout.decode("utf-8")
        self.stderr = stderr.decode("utf-8")

    def set_nodes(self, node_list):
        self.node_list = node_list
        self.num_nodes = len(node_list)

    def read_data(self):
        return []

    def get_bench_name(self):
        return ""

    def get_bench_input(self):
        return ""

    def run_app(self):
        path = self.get_binary_path()
        if path is not None:
            return path + " " + self.args
        else:
            return ""
