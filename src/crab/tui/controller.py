import os
import threading
from typing import Callable, Dict

from ..core.engine import Engine
from ..log import get_logger, TUIHandler, CrabLogger
from ..setup import memory


class TUIController:
    def __init__(self, log_callback: Callable[[str], None]):
        # Build a logger that routes records to the TUI widget
        self.logger = get_logger()
        tui_handler = TUIHandler(callback=log_callback)
        self.logger.add_handler(tui_handler)

    def _prepare_environment(self, tui_settings: Dict, selected_preset: str) -> Dict[str, str]:
        env_vars = dict(tui_settings.get("env", {}))
        execution_env = os.environ.copy()

        if selected_preset != "Custom":
            env_vars["CRAB_SYSTEM"] = selected_preset

        for key, value in list(env_vars.items()):
            if isinstance(value, str) and value == "__CWD__":
                env_vars[key] = os.getcwd() + "/"

        execution_env.update(env_vars)

        for key, value in execution_env.items():
            if isinstance(value, str):
                execution_env[key] = os.path.expandvars(value)

        return execution_env

    def _execute_benchmark_logic(self, benchmark_config: dict, tui_settings: Dict, selected_preset: str):
        self.logger.info("Preparing to run benchmark...")

        try:
            execution_env = self._prepare_environment(tui_settings, selected_preset)
            for bench_id, receipt in memory.get_all_receipts().items():
                execution_env[f"CRAB_PATH_{bench_id.upper()}"] = receipt.get("binary_path", "")
            self.logger.info("Environment prepared")

            # Mirror what orchestrator.py does: inject preset sbatch/header into the config
            # so the engine's SLURM script generator has the cluster directives.
            g_opts = benchmark_config.setdefault("global_options", {})
            if "system_sbatch" not in g_opts:
                g_opts["system_sbatch"] = tui_settings.get("sbatch", [])
            if "system_header" not in g_opts:
                g_opts["system_header"] = tui_settings.get("header", [])

            self.logger.info("Starting benchmark engine")
            engine = Engine(logger=self.logger)
            engine.run(
                config=benchmark_config,
                environment=execution_env,
            )
            self.logger.info("Benchmark finished successfully")

        except Exception as e:
            self.logger.error(f"Benchmark engine error: {e}")

    def run_in_thread(self, benchmark_config: dict, tui_settings: Dict[str, str],
                      selected_preset: str, on_complete=None):
        def _run():
            try:
                self._execute_benchmark_logic(benchmark_config, tui_settings, selected_preset)
            finally:
                if on_complete:
                    on_complete()

        thread = threading.Thread(target=_run)
        thread.start()
