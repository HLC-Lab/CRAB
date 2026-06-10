from textual import work
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button

from .benchmark_tab_selector import BenchmarkTabSelector
from .application_form import ApplicationForm
from .local_options_form import LocalOptionsForm

class ApplicationSetup(Container):
    def __init__(self, app_ref, id: str | None = None) -> None:
        super().__init__(id=id)
        self.app_ref = app_ref

        self.benchmark_states: dict[int, dict] = {
            0: {"path": "", "args": "", "collect": False, "start": "", "end": "", "partition": ""}
        }
        self.current_benchmark = 0
        self.forms_list = [ApplicationForm(app_ref=self.app_ref, benchmark_id=0)]

        self.tab_selector = BenchmarkTabSelector(benchmark_count=1)
        self.forms_container = Container(
            *self.forms_list,
            id="benchmark-forms-container"
        )
        self.local_options_form = LocalOptionsForm()

    def compose(self) -> ComposeResult:
        yield self.tab_selector
        yield self.forms_container
        yield self.local_options_form

    def on_mount(self):
        self.current_benchmark = -1
        self.show_benchmark(0)
        self.tab_selector.update_benchmark_tabs(0)

    def save_current_form_state(self):
        # Checks the index validity
        if self.current_benchmark < 0 or self.current_benchmark >= len(self.forms_list):
            return

        current_form = self.forms_list[self.current_benchmark]
        self.benchmark_states[self.current_benchmark] = current_form.get_form_data()

    def show_benchmark(self, index: int):
        if index == self.current_benchmark:
            return

        self.save_current_form_state()
        self.current_benchmark = index

        form_to_show = self.forms_list[index]
        form_to_show.set_form_data(self.benchmark_states[index])

        # Iterate through all forms and display only the one with the correct index
        for i, form in enumerate(self.forms_list):
            form.display = (i == index)

        self.tab_selector.update_benchmark_tabs(index)

    # MODIFICATO: Logica per aggiungere un nuovo benchmark
    def add_benchmark(self):
        self.save_current_form_state()
        
        new_index = len(self.benchmark_states)
        self.benchmark_states[new_index] = {
            "path": "", "args": "", "collect": False, "start": "", "end": "", "partition": ""
        }
        
        # NUOVO: Crea la nuova istanza del form
        new_form = ApplicationForm(self.app_ref, benchmark_id=new_index)
        
        # NUOVO: Aggiungi il nuovo form alla lista e montalo nel container
        self.forms_list.append(new_form)
        self.forms_container.mount(new_form)
        
        self.tab_selector.add_benchmark()
        self.show_benchmark(new_index) # Questo lo renderà visibile e nasconderà gli altri

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("benchmark-"):
            index = int(event.button.id.split("-")[1])
            self.show_benchmark(index)
        elif event.button.id == "add-benchmark":
            self.add_benchmark()
            event.stop()

    def get_state(self) -> dict:
        self.save_current_form_state()
        result = {"apps": {k: v for k, v in self.benchmark_states.items()}}
        local_opts = self.local_options_form.get_state()
        if local_opts:
            result["local_options"] = local_opts
        return result

    async def set_state(self, state: dict):
        # Support both new format {"apps": {...}, "local_options": {...}}
        # and legacy format {0: {...}, 1: {...}}
        if "apps" in state:
            apps_state = state["apps"]
            local_opts = state.get("local_options", {})
        else:
            apps_state = state
            local_opts = {}

        # 1. Reset existing state and UI
        self.benchmark_states.clear()
        self.forms_list.clear()
        await self.forms_container.remove_children()
        await self.tab_selector.clear_benchmark_forms()
        self.local_options_form.clear()

        if not apps_state:
            self.current_benchmark = -1
            return

        # 2. Rebuild forms from apps_state
        temp_forms_to_mount = []
        for key, value in apps_state.items():
            benchmark_id = int(key)
            self.benchmark_states[benchmark_id] = value

            new_form = ApplicationForm(app_ref=self.app_ref, benchmark_id=benchmark_id)
            new_form.set_form_data(value)
            self.forms_list.append(new_form)
            temp_forms_to_mount.append(new_form)

        # 3. Rebuild tabs and mount forms
        for _ in self.benchmark_states:
            self.tab_selector.add_benchmark()

        if temp_forms_to_mount:
            await self.forms_container.mount_all(temp_forms_to_mount)

        # 4. Restore local options
        if local_opts:
            self.local_options_form.set_state(local_opts)

        self.current_benchmark = -1
        self.show_benchmark(0)

