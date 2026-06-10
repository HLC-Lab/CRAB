from textual.containers import Container, VerticalScroll, Horizontal
from textual.widgets import Button, Checkbox, Collapsible, DataTable, Input, Label, Select, TextArea
from textual import on

import subprocess


def _split_nodelist(s: str) -> list[str]:
    """Split a sinfo nodelist on top-level commas only (not inside brackets).
    e.g. 'node[001,002],gpu[01-08]' → ['node[001,002]', 'gpu[01-08]']
    """
    result, depth, current = [], 0, []
    for ch in s:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            if current:
                result.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        result.append("".join(current))
    return result


class BenchmarkOptions(VerticalScroll):
    """Un widget per configurare ed eseguire un benchmark."""

    def __init__(self, app_ref):
        super().__init__()
        self.app_ref = app_ref

    def on_mount(self) -> None:
        self.border_title = "Benchmark Configuration"
        data_table = self.query_one("#node_table", DataTable)
        self._node_col_key = data_table.add_column("Available Nodes")
        self._set_partition_fields_visible(False)
        self.call_after_refresh(self._fit_node_col)

    def _fit_node_col(self) -> None:
        """Resize the node table column to fill the table's actual rendered width."""
        try:
            table = self.query_one("#node_table", DataTable)
            col = table.columns.get(self._node_col_key)
            if col is None or table.size.width == 0:
                return
            # cell_padding applied on both sides of the column, plus 2 for row-label column
            col.width = max(10, table.size.width - 2 * table.cell_padding - 2)
            col.auto_width = False
            table.refresh()
        except Exception:
            pass

    def on_show(self) -> None:
        """Fires when the tab becomes visible — size is now valid."""
        self.call_after_refresh(self._fit_node_col)

    def on_resize(self) -> None:
        self.call_after_refresh(self._fit_node_col)


    def compose(self):
        """Crea i widget figli per il form delle opzioni."""

        # ── Node Selection (always visible) ───────────────────────────────────
        with Horizontal(classes="node-row"):
            with Container(classes="node-controls"):
                yield Label("Nodes:", classes="option-label")
                yield Select([
                    ("All Nodes", "auto"),
                    ("Mixed Nodes", "mixed"),
                    ("Idle Nodes", "idle"),
                    ("From File", "file")
                ], value="auto", id="nodes", classes="node-select")
                yield Input(placeholder="Path to node list file", id="node_file", classes="node-select")
            with Container(classes="node-table-area"):
                yield DataTable(id="node_table", classes="datatable")

        # ── Job ───────────────────────────────────────────────────────────────
        with Collapsible(title="Job", collapsed=False, classes="bench-section"):
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Number of Nodes:", classes="option-label")
                    yield Input(placeholder="e.g., 4", id="numnodes", type="integer", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Processes Per Node:", classes="option-label")
                    yield Input(value="1", id="ppn", type="integer", classes="option-input")
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Job Name:", classes="option-label")
                    yield Input(placeholder="Optional name for the output folder", id="name", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Walltime:", classes="option-label")
                    yield Input(value="00:10:00", id="walltime", classes="option-input")

        # ── Allocation ────────────────────────────────────────────────────────
        with Collapsible(title="Allocation", collapsed=False, classes="bench-section"):
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Allocation Mode:", classes="option-label")
                    yield Select([
                        ("Linear", "l"),
                        ("Interleaved", "i"),
                        ("Partitioned", "p"),
                    ], value="l", id="allocationmode", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Allocation Split:", classes="option-label")
                    yield Input(placeholder="e.g., 50:50 or 'e'", value="e", id="allocationsplit", classes="option-input")
            with Horizontal(classes="options-row", id="partition-row"):
                with Container(classes="option-group"):
                    yield Label("Partition Split:", classes="option-label")
                    yield Input(placeholder="e.g., 60:40 or 'e'", value="e", id="partitionsplit", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Partition Layout:", classes="option-label")
                    yield Select([
                        ("Linear", "l"),
                        ("Interleaved", "i"),
                    ], value="l", id="partitionlayout", classes="option-input")

        # ── Convergence ───────────────────────────────────────────────────────
        with Collapsible(title="Convergence", collapsed=False, classes="bench-section"):
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Minimum Runs:", classes="option-label")
                    yield Input(value="10", id="minruns", type="integer", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Maximum Runs:", classes="option-label")
                    yield Input(value="1000", id="maxruns", type="integer", classes="option-input")
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Timeout (seconds):", classes="option-label")
                    yield Input(value="100.0", id="timeout", type="number", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Converge All Metrics:", classes="option-label")
                    yield Checkbox("Yes", id="convergeall", value=True, classes="bench-check")
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Alpha (Confidence):", classes="option-label")
                    yield Input(value="0.05", id="alpha", type="number", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Beta (Convergence):", classes="option-label")
                    yield Input(value="0.05", id="beta", type="number", classes="option-input")

        # ── Output & Files ────────────────────────────────────────────────────
        with Collapsible(title="Output & Files", collapsed=True, classes="bench-section"):
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Output Format:", classes="option-label")
                    yield Select([
                        ("CSV", "csv"),
                        ("HDF5", "hdf")
                    ], value="csv", id="outformat", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Runtime Output:", classes="option-label")
                    yield Select([
                        ("Standard Output", "stdout"),
                        ("None", "none"),
                        ("File", "file"),
                        ("Append to File", "+file")
                    ], value="stdout", id="runtimeout", classes="option-input")
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Retain Run Files:", classes="option-label")
                    yield Checkbox("Yes", id="retain_files", value=True, classes="bench-check")
                with Container(classes="option-group"):
                    yield Label("Random Seed:", classes="option-label")
                    yield Input(value="1", id="seed", type="integer", classes="option-input")
            with Horizontal(classes="options-row"):
                with Container(classes="option-group"):
                    yield Label("Data Path:", classes="option-label")
                    yield Input(value="./data", id="datapath", classes="option-input")
                with Container(classes="option-group"):
                    yield Label("Extra Info:", classes="option-label")
                    yield Input(placeholder="Details of this execution", id="extrainfo", classes="option-input")
            with Container(classes="option-group"):
                yield Label("Tags:", classes="option-label")
                yield Input(placeholder="Space-separated tags", id="tags", classes="option-input")

        # ── Advanced ──────────────────────────────────────────────────────────
        with Collapsible(title="Advanced", collapsed=True, classes="bench-section"):
            with Container(classes="option-group"):
                yield Label("Replace Mix Args:", classes="option-label")
                yield Input(placeholder="e.g., server:1.2.3.4,client:5.6.7.8", id="replace_mix_args", classes="option-input")
            with Container(classes="option-group"):
                yield Label("SBATCH Directives:", classes="option-label")
                yield TextArea(id="sbatch_directives", classes="option-input")


    def _set_partition_fields_visible(self, visible: bool) -> None:
        self.query_one("#partition-row").display = visible

    def get_state(self) -> dict:
        _UI_ONLY = {"nodes", "node_file"}
        state = {}
        for widget in list(self.query(".option-input")) + list(self.query(".bench-check")):
            if not widget.id or widget.id in _UI_ONLY:
                continue
            if isinstance(widget, TextArea):
                state[widget.id] = [l.strip() for l in widget.text.splitlines() if l.strip()]
            else:
                state[widget.id] = widget.value

        if not state.get("numnodes"):
            state["numnodes"] = "1"

        return state

    def set_state(self, state: dict) -> None:
        if not state:
            return
        for widget_id, value in state.items():
            try:
                widget = self.query_one(f"#{widget_id}", (Input, Select, Checkbox, TextArea))
                if isinstance(widget, TextArea):
                    if isinstance(value, list):
                        text = "\n".join(value)
                    elif isinstance(value, dict):
                        # Legacy CLI format: {"account": "x", "exclusive": true}
                        lines = []
                        for k, v in value.items():
                            if v is True:
                                lines.append(f"--{k}")
                            elif v is not False:
                                lines.append(f"--{k}={v}")
                        text = "\n".join(lines)
                    else:
                        text = str(value)
                    widget.text = text
                else:
                    widget.value = value
            except Exception as e:
                self.app.log(f"Could not set state for widget '{widget_id}': {e}")

        self._set_partition_fields_visible(state.get("allocationmode") == "p")


    @on(Select.Changed)
    def on_select_changed(self, event: Select.Changed) -> None:
        """Gestisce i cambiamenti nelle selezioni."""
        if event.select.id == "allocationmode":
            self._set_partition_fields_visible(event.value == "p")

        if event.select.id == "nodes":
            node_file_input = self.query_one("#node_file", Input)

            data_table = self.query_one("#node_table", DataTable)
            data_table.clear()

            if event.value == "file":
                node_file_input.visible = True
                data_table.visible = False
            else:
                data_table.visible = True
                node_file_input.visible = False
                node_file_input.value = ""

                sinfo_filter = {"mixed": ["-t", "mix"], "idle": ["-t", "idle"]}.get(event.value, [])
                try:
                    nodelist = subprocess.check_output(
                        ["sinfo", "-h", "-o", "%N"] + sinfo_filter, text=True
                    ).strip()
                    nodes = [n for n in nodelist.split("\n") if n]
                except (FileNotFoundError, subprocess.CalledProcessError):
                    nodes = []

                if nodes:
                    tokens = []
                    for line in nodes:
                        tokens.extend(_split_nodelist(line))
                    MAX_ROWS = 100
                    overflow = len(tokens) - MAX_ROWS
                    for token in tokens[:MAX_ROWS]:
                        data_table.add_row(token)
                    if overflow > 0:
                        data_table.add_row(f"(+ {overflow} more node groups)")
                else:
                    data_table.add_row("sinfo unavailable or no nodes found.")
                self.call_after_refresh(self._fit_node_col)

