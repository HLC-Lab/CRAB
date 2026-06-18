from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label

from .app_strip import AppStrip
from .application_form import ApplicationForm
from .confirm_modal import ConfirmModal
from .experiment_strip import ExperimentStrip
from .local_options_form import LocalOptionsForm


_EMPTY_APP: dict = {
    "path": "", "args": "", "collect": False,
    "start": "0", "end": "", "partition": "",
}


def _empty_experiment(name: str) -> dict:
    return {
        "name": name,
        "description": "",
        "local_options": {},
        "apps": {0: _EMPTY_APP.copy()},
    }


class ExperimentsPanel(Container):
    """
    Full Experiments tab.

    Internal state:
        experiments  — ordered list of experiment dicts
        _current_exp — index into experiments list
        _current_app — index of currently shown app within the current experiment
    """

    def __init__(self, app_ref) -> None:
        super().__init__()
        self.app_ref = app_ref

        self.experiments: list[dict] = [_empty_experiment("experiment_1")]
        self._current_exp: int = 0
        self._current_app: int = 0

        # Live form widgets — built once in compose(), reused for all experiments/apps
        self._exp_strip = ExperimentStrip()
        self._app_strip = AppStrip()
        self._app_form = ApplicationForm(app_ref=app_ref, benchmark_id=0)
        self._local_opts = LocalOptionsForm()

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield self._exp_strip
        with Container(id="exp-identity"):
            with Horizontal(classes="exp-meta-row"):
                with Container(classes="exp-meta-group"):
                    yield Label("Experiment name:", classes="exp-meta-label")
                    yield Input(
                        placeholder="experiment_name",
                        id="exp-name-input",
                        classes="exp-meta-input",
                    )
                with Container(classes="exp-meta-group"):
                    yield Label("Description (optional):", classes="exp-meta-label")
                    yield Input(
                        placeholder="free text note stored in config.json",
                        id="exp-desc-input",
                        classes="exp-meta-input",
                    )
        yield self._app_strip
        with VerticalScroll(id="exp-content-scroll"):
            yield self._app_form
            yield self._local_opts

    async def on_mount(self) -> None:
        await self._rebuild_exp_strip()
        await self._load_experiment(0)

    # ── Experiment strip ──────────────────────────────────────────────────────

    async def _rebuild_exp_strip(self) -> None:
        for exp in self.experiments:
            await self._exp_strip.add_experiment(exp["name"])
        self._exp_strip.select_tab(self._current_exp)

    @on(Button.Pressed, "#add-experiment")
    @work
    async def _add_experiment(self) -> None:
        self._save_current_state()
        n = len(self.experiments) + 1
        name = f"experiment_{n}"
        exp = _empty_experiment(name)
        self.experiments.append(exp)
        await self._exp_strip.add_experiment(name)
        await self._switch_to_experiment(len(self.experiments) - 1)

    @on(ExperimentStrip.ExperimentSelected)
    @work
    async def _on_exp_selected(self, msg: ExperimentStrip.ExperimentSelected) -> None:
        if msg.index != self._current_exp:
            self._save_current_state()
            await self._switch_to_experiment(msg.index)

    @on(ExperimentStrip.ExperimentDeleteRequested)
    @work
    async def _on_exp_delete_requested(
        self, msg: ExperimentStrip.ExperimentDeleteRequested
    ) -> None:
        if len(self.experiments) <= 1:
            self.app.notify("Cannot delete the last experiment.", severity="warning")
            return

        exp_name = self.experiments[msg.index]["name"]
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Delete experiment "{exp_name}"?')
        )
        if not confirmed:
            return

        self.experiments.pop(msg.index)

        # Full strip rebuild to keep IDs in sync
        await self._exp_strip.clear_all()
        for exp in self.experiments:
            await self._exp_strip.add_experiment(exp["name"])

        new_idx = max(0, min(self._current_exp, len(self.experiments) - 1))
        self._current_exp = -1  # force reload
        await self._switch_to_experiment(new_idx)

    # ── Experiment identity fields ────────────────────────────────────────────

    @on(Input.Changed, "#exp-name-input")
    def _on_name_changed(self, event: Input.Changed) -> None:
        if self._current_exp < 0 or self._current_exp >= len(self.experiments):
            return
        name = event.value.strip() or f"experiment_{self._current_exp + 1}"
        self.experiments[self._current_exp]["name"] = name
        self._exp_strip.rename_tab(self._current_exp, name)

    @on(Input.Changed, "#exp-desc-input")
    def _on_desc_changed(self, event: Input.Changed) -> None:
        if self._current_exp < 0 or self._current_exp >= len(self.experiments):
            return
        self.experiments[self._current_exp]["description"] = event.value

    # ── App strip ─────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#add-app")
    @work
    async def _add_app(self) -> None:
        self._save_current_app()
        exp = self.experiments[self._current_exp]
        apps = exp["apps"]
        new_idx = max(apps.keys()) + 1 if apps else 0
        apps[new_idx] = _EMPTY_APP.copy()
        await self._app_strip.add_app(new_idx)
        self._switch_to_app(new_idx)

    @on(AppStrip.AppSelected)
    def _on_app_selected(self, msg: AppStrip.AppSelected) -> None:
        if msg.index != self._current_app:
            self._save_current_app()
            self._switch_to_app(msg.index)

    @on(AppStrip.AppDeleteRequested)
    @work
    async def _on_app_delete(self, msg: AppStrip.AppDeleteRequested) -> None:
        exp = self.experiments[self._current_exp]
        apps = exp["apps"]
        if len(apps) <= 1:
            self.app.notify("Cannot delete the last application.", severity="warning")
            return

        del apps[msg.index]

        # Re-key apps as 0, 1, 2, ...
        old_keys = sorted(apps.keys())
        new_apps = {i: apps[k] for i, k in enumerate(old_keys)}
        exp["apps"] = new_apps

        # Await removal before remounting to prevent duplicate IDs
        await self._rebuild_app_strip(exp)

        new_app_idx = max(0, min(self._current_app, len(new_apps) - 1))
        self._current_app = -1  # force reload
        self._switch_to_app(new_app_idx)

    # ── Internal state helpers ────────────────────────────────────────────────

    async def _switch_to_experiment(self, index: int) -> None:
        self._current_exp = index
        self._exp_strip.select_tab(index)
        await self._load_experiment(index)

    async def _load_experiment(self, index: int) -> None:
        exp = self.experiments[index]

        # Identity fields
        self.query_one("#exp-name-input", Input).value = exp.get("name", "")
        self.query_one("#exp-desc-input", Input).value = exp.get("description", "")

        # Await removal before remounting to prevent duplicate IDs
        await self._rebuild_app_strip(exp)

        # Load first app
        self._current_app = -1
        first_idx = min(exp["apps"].keys()) if exp["apps"] else 0
        self._switch_to_app(first_idx)

        # Local options
        self._local_opts.clear()
        lo = exp.get("local_options", {})
        if lo:
            self._local_opts.set_state(lo)

    async def _rebuild_app_strip(self, exp: dict) -> None:
        await self._app_strip.clear_all()
        for idx in sorted(exp["apps"].keys()):
            await self._app_strip.add_app(idx)

    def _switch_to_app(self, index: int) -> None:
        self._current_app = index
        self._app_strip.select_tab(index)
        exp = self.experiments[self._current_exp]
        data = exp["apps"].get(index, _EMPTY_APP.copy())
        self._app_form.set_form_data(data)

    def _save_current_app(self) -> None:
        if self._current_app < 0:
            return
        exp = self.experiments[self._current_exp]
        if self._current_app in exp["apps"]:
            exp["apps"][self._current_app] = self._app_form.get_form_data()

    def _save_current_state(self) -> None:
        """Flush the active form + local options into self.experiments."""
        if self._current_exp < 0 or self._current_exp >= len(self.experiments):
            return
        self._save_current_app()
        exp = self.experiments[self._current_exp]
        lo = self._local_opts.get_state()
        exp["local_options"] = lo

    # ── Public API (called by app.py for save/load/run) ───────────────────────

    def get_state(self) -> dict:
        """Return the experiments dict in native CRAB format."""
        self._save_current_state()
        result = {}
        for exp in self.experiments:
            name = exp["name"]
            entry: dict = {"apps": {str(k): v for k, v in sorted(exp["apps"].items())}}
            if exp.get("description"):
                entry["description"] = exp["description"]
            lo = exp.get("local_options", {})
            if lo:
                entry["local_options"] = lo
            result[name] = entry
        return result

    async def set_state(self, experiments: dict) -> None:
        """Load from a native CRAB experiments dict."""
        self.experiments = []
        await self._exp_strip.clear_all()
        self._current_exp = -1
        self._current_app = -1

        for exp_name, exp_data in experiments.items():
            apps_raw = exp_data.get("apps", {})
            apps = {int(k): v for k, v in apps_raw.items()}
            if not apps:
                apps = {0: _EMPTY_APP.copy()}
            self.experiments.append({
                "name": exp_name,
                "description": exp_data.get("description", ""),
                "local_options": exp_data.get("local_options", {}),
                "apps": apps,
            })
            await self._exp_strip.add_experiment(exp_name)

        if not self.experiments:
            self.experiments = [_empty_experiment("experiment_1")]
            await self._exp_strip.add_experiment("experiment_1")

        await self._switch_to_experiment(0)

    async def reset(self) -> None:
        """Reset to a single blank experiment."""
        self.experiments = [_empty_experiment("experiment_1")]
        await self._exp_strip.clear_all()
        await self._exp_strip.add_experiment("experiment_1")
        self._current_exp = -1
        self._current_app = -1
        await self._switch_to_experiment(0)
