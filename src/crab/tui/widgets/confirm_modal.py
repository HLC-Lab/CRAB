from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    """A simple yes/no confirmation dialog. Returns True if confirmed."""

    def __init__(self, message: str) -> None:
        self._message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Delete", id="confirm-yes", variant="error")
                yield Button("Cancel", id="confirm-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
