import json
import os

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Select, Static, TabbedContent, TabPane, TextArea

from ..constants import PRESETS_FILE
from .variable_row import VariableRow


class EnvironmentSettings(Container):
    class EnvChanged(Message):
        def __init__(self, new_env: dict):
            self.new_env = new_env
            super().__init__()

    def __init__(self):
        super().__init__()
        self.presets = self._load_presets()

        # Load logic (.env) remains same...
        selected_preset = ""
        if os.path.exists(".env"):
            try:
                with open(".env") as f:
                    selected_preset = f.read().strip()
            except Exception:
                pass
        else:
            selected_preset = "local"

        if selected_preset not in self.presets:
            selected_preset = "local"  # Fallback sicuro
        self.current_preset_name = selected_preset

    def _load_presets(self) -> dict:
        try:
            with open(PRESETS_FILE) as f:
                return json.load(f)
        except Exception:
            return {"local": {"env": {}, "sbatch": [], "header": []}}

    def _save_presets(self):
        with open(PRESETS_FILE, "w") as f:
            json.dump(self.presets, f, indent=4)

    def compose(self) -> ComposeResult:
        # Top Bar (Select + Save)
        preset_options = [(name, name) for name in self.presets if name != "Custom"]
        preset_options.append(("Custom", "Custom"))

        with Horizontal(classes="top_bar"):
            yield Static("Presets:", classes="label")
            yield Select(preset_options, value=self.current_preset_name, id="preset_select")
            yield Static("", classes="spacer")
            with Horizontal(id="custom_save_area", classes="hidden"):
                yield Button("Save", id="save_preset_btn", variant="success")
                yield Input(placeholder="New Preset Name...", id="custom_preset_name")

        # Main Content with Tabs
        with TabbedContent():
            # TAB 1: Environment Variables (Existing logic)
            with TabPane("Environment Variables", id="tab_env"):
                yield VerticalScroll(id="variable_list")
                yield Button("+ Add Variable", id="add_variable_btn", variant="primary")

            # TAB 2: SBATCH Directives (New)
            with TabPane("SBATCH Defaults", id="tab_sbatch"):
                yield Static(
                    "Enter one directive per line (e.g. --partition=boost_usr_prod)",
                    classes="help_text",
                )
                yield TextArea(id="sbatch_area", language="bash")

            # TAB 3: Header Commands (New)
            with TabPane("Header Commands", id="tab_header"):
                yield Static(
                    "Shell commands to run before python (e.g. module load ...)",
                    classes="help_text",
                )
                yield TextArea(id="header_area", language="bash")

    def on_mount(self) -> None:
        self.load_preset(self.current_preset_name)

    def load_preset(self, name: str):
        # 1. Load ENV (Merge _common + preset)
        container = self.query_one("#variable_list")
        container.remove_children()

        common_data = self.presets.get("_common", {})
        preset_data = self.presets.get(name, {})

        # Helper per gestire la retrocompatibilità (se il json è vecchio/piatto)
        def get_env(data):
            return data.get("env", data) if "env" in data else data

        def get_list(data, key):
            return data.get(key, [])

        final_env = get_env(common_data).copy()
        final_env.update(get_env(preset_data))

        for key, value in final_env.items():
            if isinstance(value, str):  # Safety check
                container.mount(VariableRow(key, value))

        # 2. Load SBATCH & HEADER (Direct load, logicamente separiamo common e preset nell'UI?
        # Per semplicità di editing, mostriamo l'unione modificabile o solo quelli del preset?
        # DECISIONE: In Custom Mode si edita tutto. In Read Mode mostriamo tutto.)

        # Nota: Qui mostriamo la lista completa flatten per semplicità di editing
        full_sbatch = get_list(common_data, "sbatch") + get_list(preset_data, "sbatch")
        full_header = get_list(common_data, "header") + get_list(preset_data, "header")

        self.query_one("#sbatch_area", TextArea).text = "\n".join(full_sbatch)
        self.query_one("#header_area", TextArea).text = "\n".join(full_header)

        self._notify_change()

    def _gather_current_state(self):
        # Env Rows
        rows = self.query(VariableRow)
        env_dict = {row.key: row.value for row in rows if row.key}

        # Text Areas
        sbatch_text = self.query_one("#sbatch_area", TextArea).text
        sbatch_list = [line.strip() for line in sbatch_text.splitlines() if line.strip()]

        header_text = self.query_one("#header_area", TextArea).text
        header_list = [line.strip() for line in header_text.splitlines() if line.strip()]

        return {"env": env_dict, "sbatch": sbatch_list, "header": header_list}

    # Metodo per salvare il preset custom
    def save_custom_preset(self):
        name_input = self.query_one("#custom_preset_name", Input)
        new_name = name_input.value.strip()
        if not new_name or new_name == "Custom":
            return

        # Salviamo la struttura completa
        self.presets[new_name] = self._gather_current_state()
        self._save_presets()
        # ... update UI options ...
        self.app.notify(f"Preset '{new_name}' saved.")

    def _notify_change(self):
        self.post_message(self.EnvChanged(self._gather_current_state()))

    # ── Event Handlers ────────────────────────────────────────────────────────

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "preset_select":
            return
        event.stop()
        selected = str(event.value)
        custom_area = self.query_one("#custom_save_area")
        if selected == "Custom":
            custom_area.remove_class("hidden")
        else:
            custom_area.add_class("hidden")
            self.current_preset_name = selected
            self.load_preset(selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add_variable_btn":
            event.stop()
            self.query_one("#variable_list").mount(VariableRow("", ""))
            self._notify_change()
        elif event.button.id == "save_preset_btn":
            event.stop()
            self.save_custom_preset()

    def on_variable_row_deleted(self, message: VariableRow.Deleted) -> None:
        message.row_widget.remove()
        self._notify_change()

    def on_variable_row_changed(self, message: VariableRow.Changed) -> None:
        self._notify_change()
