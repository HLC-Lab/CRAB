from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input


class PartitionEditor(Vertical):
    """Row-based editor for named node partitions: {name: [node, ...]}."""

    class Changed(Message):
        """Posted when partition names change (row added, renamed, or deleted)."""
        def __init__(self, names: list[str]) -> None:
            self.names = names
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._count = 0

    def compose(self) -> ComposeResult:
        yield Button("+ Add Partition", id="add-partition-btn", classes="add-partition-btn")

    async def add_row(self, name: str = "", nodes: str = "") -> None:
        idx = self._count
        self._count += 1
        row = Horizontal(id=f"part-row-{idx}", classes="partition-row")
        await self.mount(row, before="#add-partition-btn")
        await row.mount(
            Input(value=name, placeholder="name", id=f"part-name-{idx}", classes="part-name-input"),
            Input(value=nodes, placeholder="node001,node002,...", id=f"part-nodes-{idx}", classes="part-nodes-input"),
            Button("✕", id=f"part-del-{idx}", classes="part-del-btn"),
        )

    def get_state(self) -> dict:
        """Return {name: [node...]} for all non-empty partition rows."""
        result = {}
        for row in self.query(".partition-row"):
            try:
                idx = int((row.id or "").split("-")[-1])
                name = self.query_one(f"#part-name-{idx}", Input).value.strip()
                nodes_str = self.query_one(f"#part-nodes-{idx}", Input).value.strip()
                if name:
                    result[name] = [n.strip() for n in nodes_str.split(",") if n.strip()]
            except Exception:
                pass
        return result

    def get_names(self) -> list[str]:
        """Return non-empty partition names in row order."""
        names = []
        for row in self.query(".partition-row"):
            try:
                idx = int((row.id or "").split("-")[-1])
                name = self.query_one(f"#part-name-{idx}", Input).value.strip()
                if name:
                    names.append(name)
            except Exception:
                pass
        return names

    async def set_state(self, state: dict) -> None:
        """Load from {name: [node...]} dict, replacing all rows."""
        for row in list(self.query(".partition-row")):
            await row.remove()
        self._count = 0
        for name, nodes in state.items():
            nodes_str = ",".join(str(n) for n in nodes) if isinstance(nodes, list) else str(nodes)
            await self.add_row(name=name, nodes=nodes_str)

    @on(Button.Pressed, "#add-partition-btn")
    @work
    async def _add_partition(self) -> None:
        await self.add_row()

    @on(Button.Pressed, ".part-del-btn")
    @work
    async def _delete_partition(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        try:
            idx = int(btn_id.split("-")[-1])
            row = self.query_one(f"#part-row-{idx}")
            await row.remove()
            self.post_message(self.Changed(self.get_names()))
        except Exception:
            pass

    @on(Input.Blurred, ".part-name-input")
    def _on_name_blurred(self, _event: Input.Blurred) -> None:
        self.post_message(self.Changed(self.get_names()))
