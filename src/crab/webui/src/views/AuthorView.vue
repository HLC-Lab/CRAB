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
import JsonPanel from "@/components/author/JsonPanel.vue";

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
            :experiment="sel"
            :exp-index="curExpIndex!"
            :global-allocation="d.allocation"
            :global-numnodes="d.numnodes"
            v-model:source-cluster="sourceCluster"
            @remove-experiment="removeExperiment"
          />
        </template>

        <p v-else class="empty pad">Select a section or experiment to edit.</p>
      </main>

      <JsonPanel v-if="showJson" @imported="selectAfterLoad" />
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

.layout {
  display: grid;
  grid-template-columns: 15rem minmax(45rem, 1fr) auto;
  gap: 1rem;
  align-items: start;
  min-width: 60rem;
}
.pane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
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
