from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll
from textual.message import Message
from textual.widgets import Button, Input


class ExperimentStrip(Horizontal):
    """Top strip of experiment tabs. Click an active tab to rename it inline."""

    class ExperimentSelected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class ExperimentDeleteRequested(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class ExperimentRenamed(Message):
        def __init__(self, index: int, name: str) -> None:
            self.index = index
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._count = 0
        self._selected: int = -1
        self._renaming: int = -1
        self._rename_old_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Button("◀", id="exp-nav-left", classes="exp-nav-arrow")
        with HorizontalScroll(id="exp-tabs-scroll"):
            yield Button("+ Experiment", id="add-experiment", classes="add-exp-btn")
        yield Button("▶", id="exp-nav-right", classes="exp-nav-arrow")

    def on_mount(self) -> None:
        scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
        # Watch scroll_x so arrows update on touchpad scroll too
        self.watch(scroller, "scroll_x", self._on_scroll_x_changed, init=False)
        self.call_after_refresh(self._update_arrows)

    def on_resize(self) -> None:
        self.call_after_refresh(self._update_arrows)

    def _on_scroll_x_changed(self, _value: float) -> None:
        self._update_arrows()

    def _update_arrows(self) -> None:
        try:
            scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
            self.query_one("#exp-nav-left", Button).display = scroller.scroll_x > 0
            self.query_one("#exp-nav-right", Button).display = (
                scroller.scroll_x < scroller.max_scroll_x
            )
        except Exception:
            pass

    async def add_experiment(self, name: str) -> None:
        idx = self._count
        self._count += 1
        scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
        btn = Button(name, id=f"exp-{idx}", classes="exp-tab")
        inp = Input(value=name, id=f"exp-rename-{idx}", classes="exp-rename-input")
        inp.display = False
        close = Button("✕", id=f"exp-close-{idx}", classes="exp-close-btn")
        await scroller.mount(btn, before="#add-experiment")
        await scroller.mount(inp, before="#add-experiment")
        await scroller.mount(close, before="#add-experiment")
        self.call_after_refresh(self._update_arrows)

    def rename_tab(self, index: int, name: str) -> None:
        try:
            self.query_one(f"#exp-{index}", Button).label = name
        except Exception:
            pass

    def select_tab(self, index: int) -> None:
        if self._renaming >= 0:
            self._exit_rename_mode(self._renaming, save=True)
        self._selected = index
        for btn in self.query(".exp-tab"):
            btn.remove_class("-primary")
            btn.variant = "default"
        try:
            self.query_one(f"#exp-{index}", Button).variant = "primary"
        except Exception:
            pass

    def _enter_rename_mode(self, index: int) -> None:
        self._renaming = index
        try:
            btn = self.query_one(f"#exp-{index}", Button)
            inp = self.query_one(f"#exp-rename-{index}", Input)
            self._rename_old_name = str(btn.label)
            inp.value = self._rename_old_name
            btn.display = False
            inp.display = True
            inp.focus()
        except Exception:
            self._renaming = -1
            self._rename_old_name = None

    def _exit_rename_mode(self, index: int, save: bool = True) -> None:
        try:
            btn = self.query_one(f"#exp-{index}", Button)
            inp = self.query_one(f"#exp-rename-{index}", Input)
            if save:
                new_name = inp.value.strip()
                if new_name:
                    btn.label = new_name
                    self.post_message(self.ExperimentRenamed(index, new_name))
                elif self._rename_old_name:
                    btn.label = self._rename_old_name
            inp.display = False
            btn.display = True
        except Exception:
            pass
        self._renaming = -1
        self._rename_old_name = None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "add-experiment":
            return  # let event bubble to ExperimentsPanel
        if btn_id == "exp-nav-left":
            event.stop()
            scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
            scroller.scroll_to(x=max(0.0, scroller.scroll_x - 20), animate=False)
            return
        if btn_id == "exp-nav-right":
            event.stop()
            scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
            scroller.scroll_to(x=scroller.scroll_x + 20, animate=False)
            return
        if btn_id.startswith("exp-close-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            self.post_message(self.ExperimentDeleteRequested(idx))
        elif btn_id.startswith("exp-"):
            idx = int(btn_id.split("-")[-1])
            event.stop()
            if self._renaming >= 0 and self._renaming != idx:
                self._exit_rename_mode(self._renaming, save=True)
            if idx == self._selected and self._renaming < 0:
                self._enter_rename_mode(idx)
            else:
                self.post_message(self.ExperimentSelected(idx))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        inp_id = event.input.id or ""
        if inp_id.startswith("exp-rename-"):
            idx = int(inp_id.split("-")[-1])
            event.stop()
            self._exit_rename_mode(idx, save=True)

    async def clear_all(self) -> None:
        try:
            scroller = self.query_one("#exp-tabs-scroll", HorizontalScroll)
        except Exception:
            return
        for child in list(scroller.children):
            if child.id != "add-experiment":
                await child.remove()
        scroller.scroll_to(x=0, animate=False)
        self._count = 0
        self._selected = -1
        self._renaming = -1
        self._rename_old_name = None
        self.call_after_refresh(self._update_arrows)
