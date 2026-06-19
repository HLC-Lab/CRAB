<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useAuthorStore } from "@/stores/author";

const store = useAuthorStore();
const d = store.draft;

const selectedIndex = ref<number | null>(null);
const sel = computed(() =>
  selectedIndex.value !== null ? d.experiments[selectedIndex.value] ?? null : null,
);

const showJson = ref(false);
const showImport = ref(false);
const importText = ref("");
const copied = ref(false);

onMounted(() => store.loadLibrary());

function selectFirstOrNone() {
  selectedIndex.value = d.experiments.length ? 0 : null;
}

function newConfig() {
  store.newConfig();
  selectedIndex.value = null;
}

function addExperiment() {
  d.experiments.push({
    name: `experiment_${d.experiments.length + 1}`,
    description: "",
    apps: [],
  });
  selectedIndex.value = d.experiments.length - 1;
}

function removeExperiment() {
  if (selectedIndex.value === null) return;
  d.experiments.splice(selectedIndex.value, 1);
  selectedIndex.value = d.experiments.length
    ? Math.min(selectedIndex.value, d.experiments.length - 1)
    : null;
}

async function onOpen(event: Event) {
  const id = (event.target as HTMLSelectElement).value;
  if (id) {
    await store.open(id);
    selectFirstOrNone();
  }
}

function doImport() {
  if (store.importJson(importText.value)) {
    showImport.value = false;
    importText.value = "";
    selectFirstOrNone();
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
    <header class="bar">
      <div class="grp">
        <button class="btn" @click="newConfig">+ New</button>
        <select class="btn open" :value="store.entryId ?? ''" @change="onOpen">
          <option value="">Open…</option>
          <option v-for="e in store.library" :key="e.id" :value="e.id">{{ e.name }}</option>
        </select>
        <button class="btn primary" :disabled="store.busy" @click="store.save()">
          {{ store.busy ? "Saving…" : "Save" }}
        </button>
        <button class="btn" :disabled="!store.entryId" @click="store.duplicate(store.entryId!)">
          Duplicate
        </button>
        <button class="btn danger" :disabled="!store.entryId" @click="store.remove(store.entryId!)">
          Delete
        </button>
      </div>
      <div class="grp">
        <button class="btn" @click="showImport = true">Import JSON</button>
        <button class="btn" @click="copyJson">{{ copied ? "Copied ✓" : "Copy JSON" }}</button>
        <button class="btn" :class="{ on: showJson }" @click="showJson = !showJson">{ } JSON</button>
      </div>
    </header>

    <p v-if="store.error" class="banner err">{{ store.error }}</p>

    <div class="layout">
      <!-- Left rail: the important globals + the experiments list -->
      <aside class="rail">
        <div class="globals">
          <h2>Run</h2>
          <label>Name <input v-model="d.name" placeholder="my_run" /></label>
          <label>Nodes <input v-model="d.numnodes" placeholder="8" /></label>
          <label>Procs / node <input v-model="d.ppn" /></label>
          <!-- allocation · convergence · output · advanced land in later increments -->
        </div>

        <div class="exp-head">
          <span>Experiments</span>
          <button class="icon-btn" title="Add experiment" @click="addExperiment">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
          </button>
        </div>
        <ul class="exp-list">
          <li
            v-for="(exp, i) in d.experiments"
            :key="i"
            :class="{ active: i === selectedIndex }"
            @click="selectedIndex = i"
          >
            <span class="exp-name">{{ exp.name || "untitled" }}</span>
            <span class="exp-meta">{{ exp.apps.length }} app{{ exp.apps.length === 1 ? "" : "s" }}</span>
          </li>
          <li v-if="!d.experiments.length" class="empty">No experiments yet.</li>
        </ul>
      </aside>

      <!-- Main pane: the selected experiment -->
      <main class="pane">
        <template v-if="sel">
          <div class="exp-edit">
            <label>Experiment name <input v-model="sel.name" /></label>
            <label>Description <input v-model="sel.description" placeholder="optional note" /></label>
          </div>
          <div class="apps-stub">Apps editor — next increment.</div>
          <button class="btn danger" @click="removeExperiment">Remove experiment</button>
        </template>
        <p v-else class="empty pad">Select or add an experiment to edit its apps.</p>
      </main>

      <!-- JSON view -->
      <aside v-if="showJson" class="jsonpane">
        <header>config.json</header>
        <pre>{{ store.configJson }}</pre>
      </aside>
    </div>

    <!-- Import modal -->
    <div v-if="showImport" class="modal-bg" @click.self="showImport = false">
      <div class="modal card">
        <h2>Import config JSON</h2>
        <p class="hint">Paste a config (or an example) to load it into the editor as a new draft.</p>
        <textarea v-model="importText" rows="14" placeholder='{ "global_options": { … }, "experiments": { … } }' />
        <div class="modal-actions">
          <button class="btn" @click="showImport = false">Cancel</button>
          <button class="btn primary" @click="doImport">Load</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.author { padding: 1.25rem 1.5rem; max-width: 80rem; }
.bar { display: flex; flex-wrap: wrap; gap: 1rem; justify-content: space-between; margin-bottom: 1rem; }
.grp { display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }
.btn {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.8rem; cursor: pointer; font-family: var(--mono);
}
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: 0.4; cursor: default; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger:hover:not(:disabled) { border-color: var(--danger); color: var(--danger); }
.btn.on { border-color: var(--accent); color: var(--accent); }
.btn.open { padding-right: 0.4rem; }
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid transparent; color: var(--text2);
  border-radius: var(--r); padding: 0.2rem; cursor: pointer;
}
.icon-btn:hover { border-color: var(--border); color: var(--text); }
.icon-btn svg {
  width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.75;
  stroke-linecap: round; stroke-linejoin: round;
}

.layout { display: grid; grid-template-columns: 18rem 1fr auto; gap: 1rem; align-items: start; }
.rail, .pane, .jsonpane {
  background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r2);
}
.rail { padding: 1rem; }
.globals h2 { font-family: var(--sans); font-size: 1rem; margin-bottom: 0.6rem; }
.globals label { display: flex; flex-direction: column; gap: 0.2rem; margin-bottom: 0.6rem;
  color: var(--text2); font-size: 0.78rem; }
input, textarea, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono); font-size: 0.82rem;
}
input:focus, textarea:focus { outline: none; border-color: var(--accent); }
.exp-head { display: flex; align-items: center; justify-content: space-between;
  margin: 1rem 0 0.4rem; padding-top: 0.8rem; border-top: 1px solid var(--border);
  color: var(--text2); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
.exp-list { list-style: none; display: flex; flex-direction: column; gap: 0.25rem; }
.exp-list li { display: flex; justify-content: space-between; align-items: center;
  padding: 0.4rem 0.5rem; border: 1px solid transparent; border-radius: var(--r); cursor: pointer; }
.exp-list li:hover { background: var(--bg2); }
.exp-list li.active { background: var(--bg2); border-color: var(--accent); }
.exp-meta { color: var(--text3); font-size: 0.72rem; }
.empty { color: var(--text3); font-size: 0.82rem; }
.empty.pad { padding: 2rem; }

.pane { padding: 1.25rem; min-height: 16rem; display: flex; flex-direction: column; gap: 1rem; }
.exp-edit { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.exp-edit label { display: flex; flex-direction: column; gap: 0.2rem; color: var(--text2); font-size: 0.78rem; }
.apps-stub { color: var(--text3); border: 1px dashed var(--border); border-radius: var(--r);
  padding: 1.5rem; text-align: center; }

.jsonpane { width: 26rem; max-height: 36rem; overflow: auto; }
.jsonpane header { position: sticky; top: 0; background: var(--bg2); color: var(--text2);
  padding: 0.4rem 0.75rem; font-size: 0.75rem; border-bottom: 1px solid var(--border); }
.jsonpane pre { padding: 0.75rem; font-size: 0.75rem; white-space: pre-wrap; color: var(--text2); }

.banner { padding: 0.5rem 0.75rem; border-radius: var(--r); margin-bottom: 1rem; }
.banner.err { background: rgba(245, 101, 101, 0.12); color: var(--danger); border: 1px solid var(--danger); }

.modal-bg { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { width: min(40rem, 92vw); padding: 1.25rem; }
.modal h2 { font-family: var(--sans); font-size: 1.1rem; margin-bottom: 0.3rem; }
.modal .hint { color: var(--text2); font-size: 0.8rem; margin-bottom: 0.75rem; }
.modal textarea { width: 100%; resize: vertical; }
.modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.75rem; }
.card { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r2); }
</style>
