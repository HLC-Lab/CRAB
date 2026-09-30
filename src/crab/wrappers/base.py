import shutil

import crab.setup.memory as memory


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:.0f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:f}Yi{suffix}"


class MissingBinaryError(RuntimeError):
    """No binary could be found for a wrapper from any of its sources."""


class base:
    # Version of the wrapper contract this class implements.
    wrapper_api = 1
    # Metrics read_data returns, in order: [{"name", "unit", "conv"}, ...].
    metadata: list = []
    # Sweep dimensions each result row carries (e.g. ["size"]); [] for one point per sample.
    keys: list = []
    # Executable name looked up on PATH when neither the config nor a receipt names a binary.
    executable = ""

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

    def get_extra_artifacts(self) -> list[str]:
        """Files to keep from each run, as paths or glob patterns relative to self.run_dir."""
        return []

    def get_bench_name(self):
        return ""

    def get_bench_input(self):
        return ""

    def find_binary(self) -> tuple[str | None, str]:
        """(binary, source): the app's config `binary`, else the receipt, else PATH.

        `source` is "config", "receipt" or "path"; (None, "missing") when nothing gives one.
        """
        configured = getattr(self, "binary", None)
        if configured:
            return str(configured), "config"
        from_receipt = self.get_binary_path()
        if from_receipt:
            return from_receipt, "receipt"
        if self.executable:
            found = shutil.which(self.executable)
            if found:
                return found, "path"
        return None, "missing"

    def resolve_binary(self) -> str:
        """The binary to launch (see find_binary).

        Raises:
            MissingBinaryError: naming every source that was tried.
        """
        binary, _source = self.find_binary()
        if binary:
            return binary

        tried = ["the app's 'binary' key in the config"]
        if self.benchmark_id:
            tried.append(f"receipt {self.benchmark_id!r}")
        else:
            tried.append("no receipt (the wrapper has no benchmark_id)")
        if self.executable:
            tried.append(f"{self.executable!r} on PATH")
        else:
            tried.append("no PATH lookup (the wrapper declares no executable)")
        raise MissingBinaryError(
            f"No binary for {type(self).__module__} (app {self.id_num}). Tried: {'; '.join(tried)}."
        )

    def run_app(self):
        command = self.resolve_binary()
        return f"{command} {self.args}" if self.args else command
