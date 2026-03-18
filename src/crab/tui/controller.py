import os
import threading
from typing import Callable, Dict

from ..core.engine import Engine
from ..log import get_logger, TUIHandler, CrabLogger


class TUIController:
    def __init__(self, log_callback: Callable[[str], None]):
        # Build a logger that routes records to the TUI widget
        self.logger = get_logger()
        tui_handler = TUIHandler(callback=log_callback)
        self.logger.add_handler(tui_handler)

    def _prepare_environment(self, tui_settings: Dict[str, str], selected_preset: str) -> Dict[str, str]:
        execution_env = os.environ.copy()
        
        if selected_preset != "Custom":
            tui_settings["CRAB_SYSTEM"] = selected_preset

        for key, value in tui_settings.items():
            if isinstance(value, str) and value == "__CWD__":
                tui_settings[key] = os.getcwd() + "/"
        
        execution_env.update(tui_settings)

        for key, value in execution_env.items():
            if isinstance(value, str):
                execution_env[key] = os.path.expandvars(value)
        
        return execution_env

    def _execute_benchmark_logic(self, benchmark_config: dict, tui_settings: Dict[str, str], selected_preset: str):
        self.logger.info("Preparing to run benchmark...")
        
        try:
            # 1. Prepara l'ambiente
            execution_env = self._prepare_environment(tui_settings, selected_preset)
            self.logger.info("Environment prepared")

            # 2. Istanzia ed esegui il motore
            self.logger.info("Starting benchmark engine")
            engine = Engine(logger=self.logger)
            engine.run(
                config=benchmark_config,
                environment=execution_env,
            )
            self.logger.info("Benchmark finished successfully")

        except Exception as e:
            self.logger.error(f"Benchmark engine error: {e}")

    def run_in_thread(self, benchmark_config: dict, tui_settings: Dict[str, str], selected_preset: str):
        thread = threading.Thread(
            target=self._execute_benchmark_logic,
            args=(benchmark_config, tui_settings, selected_preset)
        )
        thread.start()
