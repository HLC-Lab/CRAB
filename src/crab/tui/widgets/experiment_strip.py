from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button


class ExperimentStrip(Horizontal):
    """Top strip of experiment tabs. Each tab shows the experiment name with a ✕ close button."""

    class ExperimentSelected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class ExperimentDeleteRequested(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def compose(self) -> ComposeResult:
        yield Button("+ Experiment", id="add-experiment", classes="add-exp-btn")

    async def add_experiment(self, name: str) -> None:
        idx = self._count
        self._count += 1  # increment before first await to prevent ID races
        btn = Button(name, id=f"exp-{idx}", classes="exp-tab")
        close = Button("✕", id=f"exp-close-{idx}", classes="exp-close-btn")
        await self.mount(btn, before="#add-experiment")
        await self.mount(close, before="#add-experiment")

    def rename_tab(self, index: int, name: str) -> None:
        try:
            btn = self.query_one(f"#exp-{index}", Button)
            btn.label = name
        except Exception:
            pass

    def select_tab(self, index: int) -> None:
        for btn in self.query(".exp-tab"):
            btn.remove_class("-primary")
            btn.variant = "default"
        try:
            self.query_one(f"#exp-{index}", Button).variant = "primary"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "add-experiment":
            return  # let event bubble to ExperimentsPanel
        if btn_id.startswith("exp-close-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            self.post_message(self.ExperimentDeleteRequested(idx))
        elif btn_id.startswith("exp-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            self.post_message(self.ExperimentSelected(idx))

    async def clear_all(self) -> None:
        for child in list(self.children):
            if child.id != "add-experiment":
                await child.remove()
        self._count = 0
