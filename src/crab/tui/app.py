from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Button, Header, Footer, RichLog
from textual import on, work
from textual_fspicker import FileSave, FileOpen
import json
import os

from .messages import SaveConfiguration, LoadConfiguration, RunBenchmark
from .widgets.tab_selector import TabSelector
from .widgets.experiments_panel import ExperimentsPanel
from .widgets.benchmark_options import BenchmarkOptions
from .widgets.environment_settings import EnvironmentSettings

from .controller import TUIController

class BenchmarkApp(App):
    CSS_PATH = "assets/tui.tcss"
    BINDINGS = [("q", "quit", "Quit"), ("l", "load", "Load"), ("s", "save", "Save")]

    def __init__(self):
        super().__init__()
        self.current_environment_settings = {}

        self.controller = TUIController(log_callback=self.log_to_tui)

        self.experiments_container = ExperimentsPanel(self)
        self.benchmark_container = BenchmarkOptions(self)
        self.env_container = EnvironmentSettings()
        self.log_container = Vertical(
            RichLog(id="runner-log", highlight=True, classes="runner-log-tall"),
            id="log-view-container"
        )

    def log_to_tui(self, message: str):
        log = self.query_one("#runner-log", RichLog)
        self.call_from_thread(log.write, message)

    def compose(self) -> ComposeResult:
        yield Header()
        yield TabSelector(id="tab-selector", app_ref=self)
        with Container(id="main-content-area"):
            yield self.experiments_container
            yield self.benchmark_container
            yield self.env_container
            yield self.log_container
        yield Footer()

    def on_mount(self):
        self.show_tab(0)

    def on_environment_settings_env_changed(self, message: EnvironmentSettings.EnvChanged):
        self.current_environment_settings = message.new_env

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("tab-"):
            index = int(event.button.id.split("-")[1])
            self.show_tab(index)
            event.stop()

    def show_tab(self, index: int):
        self.current_tab = index
        self.experiments_container.display = (index == 0)
        self.benchmark_container.display = (index == 1)
        self.env_container.display = (index == 2)
        self.log_container.display = (index == 3)
        self._update_tab_buttons()

    def _update_tab_buttons(self):
        for i, tab_button in enumerate(self.query(".tab")):
            tab_button.variant = "primary" if i == self.current_tab else "default"

    def key_space(self) -> None:
        self.handle_run_request()

    def key_l(self) -> None:
        self.load_form_data()

    def key_s(self) -> None:
        self.save_form_data()

    def key_escape(self) -> None:
        self.query().blur()

    @on(SaveConfiguration)
    @work
    async def save_form_data(self) -> None:
        global_options = self.benchmark_container.get_state()
        experiments = self.experiments_container.get_state()

        data_to_save = {
            "global_options": global_options,
            "experiments": experiments,
        }

        try:
            file_path = await self.push_screen_wait(FileSave())
            if not file_path:
                self.notify("Save cancelled.", severity="warning")
                return

            file_path_str = str(file_path)
            if not file_path_str.endswith(".json"):
                file_path_str += ".json"

            with open(file_path_str, "w") as f:
                json.dump(data_to_save, f, indent=4)
            self.notify(f"Saved to {os.path.basename(file_path_str)}", severity="information")

        except Exception as e:
            self.notify(f"Error saving: {e}", severity="error")

    @on(LoadConfiguration)
    @work
    async def load_form_data(self) -> None:
        try:
            file_path = await self.push_screen_wait(FileOpen())
            if not file_path:
                self.notify("Load cancelled.", severity="warning")
                return

            with open(str(file_path), "r") as f:
                data = json.load(f)

            if "global_options" not in data:
                self.notify("Invalid config: missing global_options.", severity="error")
                return

            self.benchmark_container.set_state(data["global_options"])

            if "experiments" in data:
                await self.experiments_container.set_state(data["experiments"])
            elif "applications" in data:
                # Legacy single-experiment TUI format
                apps_raw = data["applications"]
                apps = apps_raw.get("apps", apps_raw)
                local_opts = apps_raw.get("local_options", {}) if isinstance(apps_raw, dict) else {}
                legacy_exp = {
                    "experiment_1": {
                        "apps": apps,
                        "local_options": local_opts,
                    }
                }
                await self.experiments_container.set_state(legacy_exp)
            else:
                self.notify("Invalid config: missing experiments.", severity="error")
                return

            self.notify(f"Loaded {os.path.basename(str(file_path))}", severity="information")

        except FileNotFoundError:
            self.notify("File not found.", severity="error")
        except json.JSONDecodeError:
            self.notify("Invalid JSON.", severity="error")
        except Exception as e:
            self.notify(f"Error loading: {e}", severity="error")

    @on(RunBenchmark)
    @work
    async def handle_run_request(self) -> None:
        log = self.query_one("#runner-log", RichLog)
        log.clear()
        self.show_tab(3)

        global_options = self.benchmark_container.get_state()
        experiments = self.experiments_container.get_state()

        benchmark_config = {
            "global_options": global_options,
            "experiments": experiments,
        }

        tui_settings = self.current_environment_settings.copy()
        selected_preset = self.env_container.current_preset_name

        self.controller.run_in_thread(
            benchmark_config=benchmark_config,
            tui_settings=tui_settings,
            selected_preset=selected_preset,
        )
