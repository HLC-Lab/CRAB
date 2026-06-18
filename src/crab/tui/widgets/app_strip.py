from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button


class AppStrip(Horizontal):
    """Tab bar for the applications within a single experiment."""

    class AppSelected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class AppDeleteRequested(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def compose(self) -> ComposeResult:
        yield Button("+ App", id="add-app", classes="add-app-btn")

    async def add_app(self, index: int) -> None:
        self._count += 1  # increment before first await to prevent ID races
        btn = Button(f"App {index}", id=f"app-{index}", classes="app-tab")
        close = Button("✕", id=f"app-close-{index}", classes="app-close-btn")
        await self.mount(btn, before="#add-app")
        await self.mount(close, before="#add-app")

    def select_tab(self, index: int) -> None:
        for btn in self.query(".app-tab"):
            btn.variant = "default"
        try:
            self.query_one(f"#app-{index}", Button).variant = "primary"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "add-app":
            return  # let event bubble to ExperimentsPanel
        if btn_id.startswith("app-close-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            self.post_message(self.AppDeleteRequested(idx))
        elif btn_id.startswith("app-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            self.post_message(self.AppSelected(idx))

    async def clear_all(self) -> None:
        for child in list(self.children):
            if child.id != "add-app":
                await child.remove()
        self._count = 0
