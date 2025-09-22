from textual.app import ComposeResult
from textual.containers import VerticalScroll, Horizontal, Container
from textual.widgets import Button, Input, Select, Static
from textual.message import Message

from .variable_row import VariableRow
import json
import os

class EnvironmentSettings(Container):

    class EnvChanged(Message):
        """Sent when the entire environment dictionary changes."""
        def __init__(self, new_env: dict):
            self.new_env = new_env
            super().__init__()

    def __init__(self):
        super().__init__()
        self.presets = self._load_presets()

        selected_preset = ""
        # Checks if .env file exists and loads the preset name from it
        if os.path.exists(".env"):
            try:
                with open(".env", "r") as f:
                    selected_preset = f.read().strip()
            except Exception as e:
                self.log(f"Could not read .env file: {e}")
        else:
            selected_preset = "local"

        if selected_preset not in self.presets:
            raise Exception(f"Preset '{selected_preset}' not found in presets.json")

        self.current_preset_name = selected_preset 

    def _load_presets(self) -> dict:
        try:
            with open("presets.json", "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"local": {}, "Custom": {}}

    def _save_presets(self):
        with open("presets.json", "w") as f:
            json.dump(self.presets, f, indent=4)

    def compose(self) -> ComposeResult:
        preset_options = [(name, name) for name in self.presets if name != "Custom"]
        preset_options.append(("Custom", "Custom"))

        with Horizontal(classes="top_bar"):
            yield Static("Presets:", classes="label")
            yield Select(preset_options, value=self.current_preset_name, id="preset_select")
            
            yield Static("", classes="spacer")

            with Horizontal(id="custom_save_area", classes="hidden"):
                yield Button("Save", id="save_preset_btn", variant="success")
                yield Input(placeholder="New Preset Name...", id="custom_preset_name")

        yield VerticalScroll(id="variable_list")
        yield Button("+ Add Variable", id="add_variable_btn", variant="primary")





    def on_mount(self) -> None:
        self.load_preset(self.current_preset_name)

    def on_select_changed(self, event: Select.Changed) -> None:
        self.current_preset_name = str(event.value)
        self.load_preset(self.current_preset_name)
        self.query_one("#custom_save_area").set_class(self.current_preset_name != "Custom", "hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add_variable_btn":
            new_row = VariableRow()
            self.query_one("#variable_list").mount(new_row)
            new_row.scroll_visible()
        elif event.button.id == "save_preset_btn":
            self.save_custom_preset()

    @property
    def current_env_dict(self) -> dict:
        rows = self.query(VariableRow)
        return {row.key: row.value for row in rows if row.key}

    def load_preset(self, name: str):
        container = self.query_one("#variable_list")
        container.remove_children()

        # Carica le variabili comuni come base
        final_vars = self.presets.get("_common", {}).copy()

        # Carica le variabili specifiche del preset e uniscile
        preset_vars = self.presets.get(name, {})
        final_vars.update(preset_vars)

        for key, value in final_vars.items():
            container.mount(VariableRow(key, value))

        def post_update():
            self.post_message(self.EnvChanged(self.current_env_dict))
        self.call_later(post_update)

    def on_variable_row_deleted(self, message: VariableRow.Deleted):
        message.row_widget.remove()
        self.post_message(self.EnvChanged(self.current_env_dict))

    def on_variable_row_changed(self, message: VariableRow.Changed):
        self.post_message(self.EnvChanged(self.current_env_dict))
    
    def save_custom_preset(self):
        name_input = self.query_one("#custom_preset_name", Input)
        new_name = name_input.value.strip()
        if not new_name or new_name == "Custom":
            self.app.notify("Invalid preset name.", title="Error", severity="error")
            return
        
        self.presets[new_name] = self.current_env_dict
        self._save_presets()

        select = self.query_one(Select)
        new_options = [(name, name) for name in self.presets if name != "Custom"]
        new_options.append(("Custom", "Custom"))
        select.set_options(new_options)
        select.value = new_name
        self.app.notify(f"Preset '{new_name}' saved successfully.")
