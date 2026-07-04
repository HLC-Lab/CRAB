<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from "vue";
import { useAuthorStore } from "@/stores/author";
import { useRemotesStore } from "@/stores/remotes";
import { cloneExperiment, emptyExperiment, validateDraft } from "@/lib/config";
import ConfirmModal from "@/components/ConfirmModal.vue";
import LibraryBar from "@/components/author/LibraryBar.vue";
import AuthorRail from "@/components/author/AuthorRail.vue";
import BasicsPane from "@/components/author/BasicsPane.vue";
import AllocationPane from "@/components/author/AllocationPane.vue";
import RunSettingsPane from "@/components/author/RunSettingsPane.vue";
import ExperimentPane from "@/components/author/ExperimentPane.vue";

const store = useAuthorStore();
const remotes = useRemotesStore();
const d = store.draft;

// What the main pane shows: a global section or one experiment.
type GlobalId = "job" | "alloc" | "run";
const view = ref<{ kind: "global" | "exp"; id: GlobalId | number }>({ kind: "global", id: "job" });
function selectGlobal(id: GlobalId) {
  view.value = { kind: "global", id };
}
function selectExp(i: number) {
  view.value = { kind: "exp", id: i };
}
const sel = computed(() =>
  view.value.kind === "exp" ? (d.experiments[view.value.id as number] ?? null) : null,
);
// Index of the currently-selected experiment, or null when a global section
// is showing. Used to guard the "Remove experiment" action in the pane.
const curExpIndex = computed(() => (view.value.kind === "exp" ? (view.value.id as number) : null));

// -- Cluster source for the wrapper/node pickers ---------------------------
// Shared between BasicsPane (reads it, for the nodes reference) and
// ExperimentPane (owns picking it, for the wrapper catalog), so it lives here.
const connectedClusters = computed(() =>
  remotes.items.filter((r) => r.connected).map((r) => r.name),
);
const sourceCluster = ref("");
watchEffect(() => {
  if (!connectedClusters.value.includes(sourceCluster.value)) {
    sourceCluster.value = connectedClusters.value[0] ?? "";
  }
});

const issues = computed(() => validateDraft(d));
const showIssues = ref(false);

const showJson = ref(false);
const copied = ref(false);

onMounted(() => {
  store.loadLibrary();
  remotes.refresh(); // to know which clusters are connected for the pickers
});

function selectAfterLoad() {
  if (d.experiments.length) selectExp(0);
  else selectGlobal("job");
}

function addExperiment() {
  d.experiments.push(emptyExperiment(`experiment_${d.experiments.length + 1}`));
  selectExp(d.experiments.length - 1);
}

// Removes the experiment at `i` (any row, not necessarily the selected one:
// the rail hover-trash can delete a row without first navigating to it).
function removeExperiment(i: number) {
  d.experiments.splice(i, 1);
  if (view.value.kind !== "exp") return;
  const cur = view.value.id as number;
  if (cur === i) {
    if (d.experiments.length) selectExp(Math.min(i, d.experiments.length - 1));
    else selectGlobal("job");
  } else if (cur > i) {
    view.value = { kind: "exp", id: cur - 1 };
  }
}

// Rail trash icon requests removal via the confirm modal instead of removing
// immediately; the target index is held until the modal is confirmed/cancelled.
const removeExperimentTarget = ref<number | null>(null);
function requestRemoveExperiment(i: number): void {
  removeExperimentTarget.value = i;
}
function confirmRemoveExperiment(): void {
  if (removeExperimentTarget.value !== null) removeExperiment(removeExperimentTarget.value);
  removeExperimentTarget.value = null;
}

// Duplicates the experiment at `i`, inserting the copy right after it and
// selecting it. Mirrors the library's own "<name> copy" naming convention
// (see store/library.py's duplicate()).
function duplicateExperiment(i: number): void {
  const src = d.experiments[i];
  const copy = cloneExperiment(src);
  copy.name = src.name.trim() ? `${src.name.trim()} copy` : "";
  d.experiments.splice(i + 1, 0, copy);
  selectExp(i + 1);
}

const importFileInput = ref<HTMLInputElement | null>(null);

async function onImportFileChosen(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = ""; // reset so choosing the same file again still fires @change
  if (!file) return;
  const text = await file.text();
  if (store.importJson(text)) {
    selectAfterLoad();
  }
}

async function copyJson() {
  try {
    await navigator.clipboard.writeText(store.configJson);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard blocked; the JSON view is still available to copy by hand */
  }
}
</script>

<template>
  <section class="author">
    <LibraryBar v-model:show-json="showJson" @new="selectGlobal('job')" @opened="selectAfterLoad" />

    <p v-if="store.error" class="banner err">{{ store.error }}</p>
    <p v-if="store.notice" class="banner info">{{ store.notice }}</p>

    <!-- Non-blocking shape-validation summary -->
    <div class="validity" :class="issues.length ? 'warn' : 'ok'">
      <button v-if="issues.length" class="vbtn" @click="showIssues = !showIssues">
        ⚠ {{ issues.length }} thing{{ issues.length === 1 ? "" : "s" }} to fix
        <span class="caret">{{ showIssues ? "▾" : "▸" }}</span>
      </button>
      <span v-else class="vok">✓ Ready to submit</span>
      <ul v-if="showIssues && issues.length" class="issue-list">
        <li v-for="(msg, i) in issues" :key="i">{{ msg }}</li>
      </ul>
    </div>

    <div class="layout">
      <AuthorRail
        :view="view"
        @select-global="selectGlobal"
        @select-exp="selectExp"
        @add-experiment="addExperiment"
        @duplicate-experiment="duplicateExperiment"
        @request-remove-experiment="requestRemoveExperiment"
      />

      <!-- Main pane: the selected section or experiment, full width -->
      <main class="pane">
        <!-- GLOBAL · Basics -->
        <template v-if="view.kind === 'global' && view.id === 'job'">
          <BasicsPane :source-cluster="sourceCluster" />
        </template>

        <!-- GLOBAL · Node allocation -->
        <template v-else-if="view.kind === 'global' && view.id === 'alloc'">
          <AllocationPane />
        </template>

        <!-- GLOBAL · Run settings (convergence, output & advanced, slurm) -->
        <template v-else-if="view.kind === 'global' && view.id === 'run'">
          <RunSettingsPane />
        </template>

        <!-- EXPERIMENT -->
        <template v-else-if="sel">
          <ExperimentPane
            :exp-index="curExpIndex!"
            v-model:source-cluster="sourceCluster"
            @remove-experiment="removeExperiment"
          />
        </template>

        <p v-else class="empty pad">Select a section or experiment to edit.</p>
      </main>

      <!-- JSON view: current config (read-only, with Copy) plus load-from-file import,
           replacing what used to be two separate toolbar buttons and a modal. -->
      <aside v-if="showJson" class="jsonpane">
        <header>
          <span>config.json</span>
          <button class="link-btn" @click="copyJson">{{ copied ? "Copied ✓" : "Copy" }}</button>
        </header>
        <pre>{{ store.configJson }}</pre>
        <div class="jsonpane-import">
          <h3>Import JSON</h3>
          <p class="hint">Load a config file from your computer into the editor as a new draft.</p>
          <input
            ref="importFileInput"
            type="file"
            accept="application/json,.json"
            class="visually-hidden"
            @change="onImportFileChosen"
          />
          <button class="btn primary" @click="importFileInput?.click()">Load from file…</button>
        </div>
      </aside>
    </div>

    <!-- Remove-experiment confirm, triggered from the rail's trash icon -->
    <ConfirmModal
      v-if="removeExperimentTarget !== null"
      title="Remove this experiment?"
      :message="`Remove ${d.experiments[removeExperimentTarget]?.name ? '“' + d.experiments[removeExperimentTarget]!.name + '”' : 'this experiment'}? This cannot be undone.`"
      confirm-label="Remove"
      @confirm="confirmRemoveExperiment"
      @cancel="removeExperimentTarget = null"
    />
  </section>
</template>

<style scoped>
.author {
  padding: 1.25rem 1.5rem;
  max-width: 98rem;
  overflow-x: auto;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.layout {
  display: grid;
  grid-template-columns: 15rem minmax(45rem, 1fr) auto;
  gap: 1rem;
  align-items: start;
  min-width: 60rem;
}
.pane,
.jsonpane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}

/* Main pane */
.pane {
  padding: 1.5rem;
  min-height: 18rem;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}
.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
.empty.pad {
  padding: 2rem;
}

.link-btn {
  background: transparent;
  border: none;
  color: var(--text2);
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  padding: 0;
}
.link-btn:hover {
  color: var(--text);
}

.jsonpane {
  width: 26rem;
  max-height: 40rem;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
.jsonpane header {
  position: sticky;
  top: 0;
  background: var(--bg2);
  color: var(--text2);
  padding: 0.4rem 0.75rem;
  font-size: var(--t-sm);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 1;
}
.jsonpane pre {
  padding: 0.75rem;
  font-size: var(--t-sm);
  white-space: pre-wrap;
  color: var(--text2);
  margin: 0;
}
.jsonpane-import {
  padding: 0.75rem;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.jsonpane-import h3 {
  font-family: var(--sans);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text3);
}
.jsonpane-import .hint {
  color: var(--text3);
  font-size: var(--t-xs);
  margin: 0;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.banner {
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
  margin-bottom: 1rem;
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
}
.banner.info {
  background: var(--accent-glow);
  color: var(--text2);
  border: 1px solid var(--border2);
}

.validity {
  margin-bottom: 1rem;
  font-size: var(--t-md);
}
.validity .vok {
  color: var(--ok);
}
.vbtn {
  background: transparent;
  border: none;
  color: var(--warn);
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-md);
  padding: 0;
}
.caret {
  color: var(--text3);
}
.issue-list {
  list-style: none;
  margin-top: 0.4rem;
  padding-left: 0.5rem;
  border-left: 2px solid var(--warn);
  color: var(--text2);
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
</style>
