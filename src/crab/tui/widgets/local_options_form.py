import json

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Collapsible, Input, Label, TextArea

_FIELDS = [
    ("numnodes", "Number of Nodes", "integer"),
    ("ppn", "Processes Per Node", "integer"),
    ("timeout", "Timeout (s)", "number"),
    ("minruns", "Min Runs", "integer"),
    ("maxruns", "Max Runs", "integer"),
]


class LocalOptionsForm(Container):
    """Per-experiment overrides. Empty fields inherit from global_options."""

    def compose(self) -> ComposeResult:
        with Collapsible(
            title="Local Options — override globals for this experiment", collapsed=True
        ):
            for field_id, label, input_type in _FIELDS:
                yield Label(f"{label}:")
                yield Input(
                    placeholder="leave empty to inherit from global",
                    id=f"lo_{field_id}",
                    type=input_type,
                    classes="lo-input",
                )
            yield Label("Allocation Override (JSON):")
            yield TextArea(
                id="lo_allocation",
                classes="lo-input-area",
            )

    def get_state(self) -> dict:
        """Return only non-empty overrides, keyed without the 'lo_' prefix."""
        state = {}
        for widget in self.query(".lo-input"):
            if widget.id and widget.value:
                state[widget.id[3:]] = widget.value
        alloc_text = self.query_one("#lo_allocation", TextArea).text.strip()
        if alloc_text:
            try:
                state["allocation"] = json.loads(alloc_text)
            except json.JSONDecodeError:
                pass
        return state

    def set_state(self, state: dict) -> None:
        for field_id, value in state.items():
            if field_id == "allocation":
                continue
            try:
                self.query_one(f"#lo_{field_id}", Input).value = str(value)
            except Exception:
                pass
        alloc = state.get("allocation")
        if alloc is not None:
            self.query_one("#lo_allocation", TextArea).load_text(json.dumps(alloc, indent=2))

    def clear(self) -> None:
        for widget in self.query(".lo-input"):
            widget.value = ""
        self.query_one("#lo_allocation", TextArea).load_text("")
