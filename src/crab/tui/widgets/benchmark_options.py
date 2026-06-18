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


def _expand_nodelist_token(token: str) -> list[str]:
    """Expand 'prefix[r1,r2,r3]' into ['prefix[r1]', 'prefix[r2]', 'prefix[r3]'].
    Each comma-separated range inside brackets becomes its own row.
    e.g. 'lrdn[0001-0026,0028-0038]' → ['lrdn[0001-0026]', 'lrdn[0028-0038]']
    Plain tokens without brackets are returned as-is.
    """
    bracket_start = token.find("[")
    bracket_end = token.rfind("]")
    if bracket_start == -1 or bracket_end == -1 or bracket_end < bracket_start:
        return [token]
    prefix = token[:bracket_start]
    inner = token[bracket_start + 1:bracket_end]
    return [f"{prefix}[{r}]" for r in inner.split(",") if r]


class BenchmarkOptions(VerticalScroll):
    """Un widget per configurare ed eseguire un benchmark."""

    def __init__(self, app_ref):
        super().__init__()
        self.app_ref = app_ref

    def on_mount(self) -> None:
        self.border_title = "Benchmark Configuration"
        data_table = self.query_one("#node_table", DataTable)
        self._node_col_key = data_table.add_column("Available Nodes")
        self._update_alloc_visibility("linear")
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
                    yield Label("Mode:", classes="option-label")
                    yield Select([
                        ("Linear", "linear"),
                        ("Interleaved", "interleaved"),
                        ("Random", "random"),
                    ], value="linear", id="alloc_mode", classes="alloc-input")
                with Container(classes="option-group"):
                    yield Label("Split:", classes="option-label")
                    yield Input(
                        placeholder='even or [50, 50]', value="even",
                        id="alloc_split", classes="alloc-input"
                    )
            with Horizontal(classes="options-row", id="alloc_stride_row"):
                with Container(classes="option-group"):
                    yield Label("Stride:", classes="option-label")
                    yield Input(value="1", id="alloc_stride", type="integer", classes="alloc-input")
            with Horizontal(classes="options-row", id="alloc_seed_row"):
                with Container(classes="option-group"):
                    yield Label("Random Seed:", classes="option-label")
                    yield Input(placeholder="Optional integer seed", id="alloc_seed", classes="alloc-input")
            with Container(classes="option-group"):
                yield Label("Partitions (JSON, optional):", classes="option-label")
                yield TextArea(id="alloc_partitions", classes="alloc-input")

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


    def _update_alloc_visibility(self, mode: str) -> None:
        self.query_one("#alloc_stride_row").display = (mode == "interleaved")
        self.query_one("#alloc_seed_row").display = (mode == "random")

    def _get_allocation_state(self) -> dict:
        import json
        alloc = {}
        mode = self.query_one("#alloc_mode", Select).value
        if mode is Select.BLANK:
            mode = "linear"
        alloc["mode"] = mode
        split_str = self.query_one("#alloc_split", Input).value.strip()
        if split_str and split_str != "even":
            try:
                alloc["split"] = json.loads(split_str)
            except json.JSONDecodeError:
                pass
        if mode == "interleaved":
            stride_str = self.query_one("#alloc_stride", Input).value.strip()
            if stride_str and stride_str != "1":
                alloc["stride"] = int(stride_str)
        if mode == "random":
            seed_str = self.query_one("#alloc_seed", Input).value.strip()
            if seed_str:
                alloc["seed"] = int(seed_str)
        partitions_text = self.query_one("#alloc_partitions", TextArea).text.strip()
        if partitions_text:
            try:
                alloc["partitions"] = json.loads(partitions_text)
            except json.JSONDecodeError:
                pass
        return alloc

    def _set_allocation_state(self, alloc: dict) -> None:
        import json
        self.query_one("#alloc_mode", Select).value = alloc.get("mode", "linear")
        split = alloc.get("split", "even")
        self.query_one("#alloc_split", Input).value = (
            json.dumps(split) if isinstance(split, list) else "even"
        )
        self.query_one("#alloc_stride", Input).value = str(alloc.get("stride", 1))
        seed = alloc.get("seed")
        self.query_one("#alloc_seed", Input).value = str(seed) if seed is not None else ""
        partitions = alloc.get("partitions")
        self.query_one("#alloc_partitions", TextArea).text = (
            json.dumps(partitions, indent=2) if partitions else ""
        )
        self._update_alloc_visibility(alloc.get("mode", "linear"))

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

        state["allocation"] = self._get_allocation_state()

        return state

    def set_state(self, state: dict) -> None:
        if not state:
            return
        for widget_id, value in state.items():
            if widget_id == "allocation":
                continue
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

        self._set_allocation_state(state.get("allocation", {}))


    @on(Select.Changed)
    def on_select_changed(self, event: Select.Changed) -> None:
        """Gestisce i cambiamenti nelle selezioni."""
        if event.select.id == "alloc_mode":
            self._update_alloc_visibility(event.value)

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
                    rows = []
                    for line in nodes:
                        for top in _split_nodelist(line):
                            rows.extend(_expand_nodelist_token(top))
                    MAX_ROWS = 200
                    overflow = len(rows) - MAX_ROWS
                    for row in rows[:MAX_ROWS]:
                        data_table.add_row(row)
                    if overflow > 0:
                        data_table.add_row(f"(+ {overflow} more ranges)")
                else:
                    data_table.add_row("sinfo unavailable or no nodes found.")
                self.call_after_refresh(self._fit_node_col)

