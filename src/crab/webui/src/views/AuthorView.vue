<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from "vue";
import { useAuthorStore } from "@/stores/author";
import { useRemotesStore } from "@/stores/remotes";
import { useCatalogStore } from "@/stores/catalog";
import { emptyApp, emptyExperiment, flowLayout, hasAllocation, validateDraft } from "@/lib/config";
import AllocationEditor from "@/components/AllocationEditor.vue";
import OptionsFields from "@/components/OptionsFields.vue";
import SbatchEditor from "@/components/SbatchEditor.vue";

const store = useAuthorStore();
const remotes = useRemotesStore();
const catalog = useCatalogStore();
const d = store.draft;

// -- Cluster source for the wrapper/node pickers ---------------------------
// Wrappers live on the cluster, so browsing them needs a connected remote. We
// auto-pick the only connected one; with several, the user chooses; with none,
// the app path stays free-text (the picker is just unavailable).
const connectedClusters = computed(() =>
  remotes.items.filter((r) => r.connected).map((r) => r.name),
);
const sourceCluster = ref("");
watchEffect(() => {
  if (!connectedClusters.value.includes(sourceCluster.value)) {
    sourceCluster.value = connectedClusters.value[0] ?? "";
  }
});

// Informational cluster-node reference (Slurm partitions; NOT allocation groups).
// Lazily fetched on first expand so opening the editor doesn't hit SSH.
const showNodes = ref(false);
const nodesInfo = computed(() => catalog.nodes[sourceCluster.value]);
function toggleNodes() {
  showNodes.value = !showNodes.value;
  if (showNodes.value && sourceCluster.value) catalog.loadNodes(sourceCluster.value);
}

const showAlloc = ref(false);
const showTuning = ref(false);
const showSbatch = ref(false);
const allocActive = computed(() => hasAllocation(d.allocation));
// Whether any tunable option has been set (drives the collapsed-section dot).
const tuningActive = computed(() => Object.values(d.options).some((v) => v !== ""));
const sbatchActive = computed(() => d.sbatch.lines.some((l) => l.trim()));

const selectedIndex = ref<number | null>(null);
const sel = computed(() =>
  selectedIndex.value !== null ? d.experiments[selectedIndex.value] ?? null : null,
);
const flow = computed(() => (sel.value ? flowLayout(sel.value.apps) : []));
const issues = computed(() => validateDraft(d));
const showIssues = ref(false);
const showOverrides = ref(false);

// Node groups an app may target = the EFFECTIVE allocation for the selected
// experiment (its local override when set, otherwise the global allocation).
const effectiveAlloc = computed(() => {
  const e = sel.value;
  // Overriding replaces the global allocation wholesale, so its groups win even
  // when bare-linear (⇒ no groups); only fall back to global when not overriding.
  return e && e.overrideAlloc ? e.allocation : d.allocation;
});
const groupNames = computed(() =>
  effectiveAlloc.value.by === "groups"
    ? effectiveAlloc.value.partitions.map((p) => p.name.trim()).filter(Boolean)
    : [],
);
// Whether the selected experiment has any local_options set (drives its dot).
const overridesActive = computed(() => {
  const e = sel.value;
  if (!e) return false;
  // overrideAlloc always emits an allocation (force, even bare linear), so it counts.
  return e.overrideAlloc || Object.values(e.options).some((v) => v !== "");
});

// Open overlay (searchable library picker)
const showOpen = ref(false);
const openQuery = ref("");
const filteredLibrary = computed(() => {
  const q = openQuery.value.trim().toLowerCase();
  const lib = store.library;
  return q ? lib.filter((e) => e.name.toLowerCase().includes(q)) : lib;
});
async function openEntry(id: string) {
  await store.open(id);
  selectFirstOrNone();
  showOpen.value = false;
  openQuery.value = "";
}

function addApp() {
  sel.value?.apps.push(emptyApp());
}
function removeApp(i: number) {
  sel.value?.apps.splice(i, 1);
}
// App indices an app can depend on (everything but itself).
function otherIndices(self: number): number[] {
  return (sel.value?.apps ?? []).map((_, j) => j).filter((j) => j !== self);
}

// -- Wrapper picker (searchable overlay over the cluster catalog) ----------
const showWrapper = ref(false);
const wrapperFor = ref<number | null>(null);
const wrapperQuery = ref("");

function openWrapperPicker(appIndex: number) {
  if (!sourceCluster.value) return;
  wrapperFor.value = appIndex;
  wrapperQuery.value = "";
  showWrapper.value = true;
  catalog.loadBenchmarks(sourceCluster.value);
}
const wrappers = computed(() => catalog.benchmarks[sourceCluster.value]?.wrappers ?? []);
const filteredWrappers = computed(() => {
  const q = wrapperQuery.value.trim().toLowerCase();
  const list = wrappers.value;
  if (!q) return list;
  return list.filter((w) =>
    [w.relpath, w.bench_name, w.benchmark_id, w.group].some((s) => s?.toLowerCase().includes(q)),
  );
});
// Group filtered wrappers by their top-level folder for display.
const wrapperGroups = computed(() => {
  const by: Record<string, typeof filteredWrappers.value> = {};
  for (const w of filteredWrappers.value) (by[w.group || "other"] ??= []).push(w);
  return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
});
function chooseWrapper(relpath: string) {
  const e = sel.value;
  if (e && wrapperFor.value !== null && e.apps[wrapperFor.value]) {
    e.apps[wrapperFor.value].path = relpath;
  }
  showWrapper.value = false;
  wrapperFor.value = null;
}

const showJson = ref(false);
const showImport = ref(false);
const importText = ref("");
const copied = ref(false);

onMounted(() => {
  store.loadLibrary();
  remotes.refresh(); // to know which clusters are connected for the pickers
});

function selectFirstOrNone() {
  selectedIndex.value = d.experiments.length ? 0 : null;
}

function newConfig() {
  store.newConfig();
  selectedIndex.value = null;
}

function addExperiment() {
  d.experiments.push(emptyExperiment(`experiment_${d.experiments.length + 1}`));
  selectedIndex.value = d.experiments.length - 1;
}

function removeExperiment() {
  if (selectedIndex.value === null) return;
  d.experiments.splice(selectedIndex.value, 1);
  selectedIndex.value = d.experiments.length
    ? Math.min(selectedIndex.value, d.experiments.length - 1)
    : null;
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
        <button class="btn" @click="showOpen = true">Open…</button>
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
      <!-- Left rail: the important globals + the experiments list -->
      <aside class="rail">
        <div class="globals">
          <h2>Use case</h2>
          <label>Use case name <input v-model="d.name" placeholder="congestion_study" /></label>
          <label>Nodes <input v-model="d.numnodes" placeholder="8" /></label>
          <label>Procs / node <input v-model="d.ppn" /></label>

          <!-- Informational cluster-node reference for sizing `Nodes` -->
          <div v-if="sourceCluster" class="nodes-ref">
            <button class="link-btn" @click="toggleNodes">
              {{ showNodes ? "▾" : "▸" }} cluster nodes · {{ sourceCluster }}
            </button>
            <div v-if="showNodes" class="nodes-body">
              <p v-if="catalog.busy[sourceCluster]" class="hint">Loading…</p>
              <p v-else-if="catalog.error[sourceCluster]" class="hint err">{{ catalog.error[sourceCluster] }}</p>
              <template v-else-if="nodesInfo">
                <p v-if="!nodesInfo.available" class="hint">{{ nodesInfo.note || "sinfo unavailable here" }}</p>
                <template v-else>
                  <p class="hint">{{ nodesInfo.nodes.length }} nodes · {{ nodesInfo.partitions.length }} partition{{ nodesInfo.partitions.length === 1 ? "" : "s" }}</p>
                  <ul class="part-list">
                    <li v-for="p in nodesInfo.partitions" :key="p.name">
                      <span class="pname">{{ p.name }}</span>
                      <span class="pmeta">{{ p.nodes ?? "?" }}<span v-if="p.avail"> · {{ p.avail }}</span></span>
                    </li>
                  </ul>
                  <p class="hint muted">Slurm partitions — distinct from allocation node groups.</p>
                </template>
              </template>
            </div>
          </div>
        </div>

        <!-- Node allocation (collapsible) -->
        <div class="section">
          <button class="sec-head" @click="showAlloc = !showAlloc">
            <span>Node allocation</span>
            <span class="sec-state">
              <span v-if="allocActive" class="dot" />
              <span class="caret">{{ showAlloc ? "▾" : "▸" }}</span>
            </span>
          </button>
          <AllocationEditor v-show="showAlloc" :alloc="d.allocation" />
        </div>

        <!-- Convergence · output · advanced (collapsible) -->
        <div class="section">
          <button class="sec-head" @click="showTuning = !showTuning">
            <span>Tuning</span>
            <span class="sec-state">
              <span v-if="tuningActive" class="dot" />
              <span class="caret">{{ showTuning ? "▾" : "▸" }}</span>
            </span>
          </button>
          <OptionsFields v-show="showTuning" :options="d.options" />
        </div>

        <!-- Slurm directives (collapsible) -->
        <div class="section">
          <button class="sec-head" @click="showSbatch = !showSbatch">
            <span>Slurm directives</span>
            <span class="sec-state">
              <span v-if="sbatchActive" class="dot" />
              <span class="caret">{{ showSbatch ? "▾" : "▸" }}</span>
            </span>
          </button>
          <SbatchEditor v-show="showSbatch" :sbatch="d.sbatch" />
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

          <!-- Abstract app flow: columns = sequential stages, colour = role -->
          <div v-if="sel.apps.length" class="flow">
            <template v-for="(col, ci) in flow" :key="ci">
              <div v-if="ci > 0" class="flow-arrow" aria-hidden="true">→</div>
              <div class="flow-col">
                <div v-for="node in col" :key="node.index" class="node" :class="node.role">
                  <span class="node-name">{{ node.name }}</span>
                  <span class="node-tags">
                    <span class="role-tag">{{ node.role }}</span>
                    <span v-if="node.note" class="tag">{{ node.note }}</span>
                    <span v-if="node.endKind === 'force'" class="tag">stops w/ others</span>
                    <span v-else-if="node.endKind === 'timed'" class="tag">timed</span>
                  </span>
                </div>
              </div>
            </template>
          </div>

          <!-- Where the wrapper picker sources its catalog -->
          <div class="wrapper-source">
            <template v-if="connectedClusters.length > 1">
              <label>Wrappers from
                <select v-model="sourceCluster">
                  <option v-for="c in connectedClusters" :key="c" :value="c">{{ c }}</option>
                </select>
              </label>
            </template>
            <span v-else-if="sourceCluster" class="src-note">Wrappers from <b>{{ sourceCluster }}</b></span>
            <span v-else class="src-note muted">Connect a cluster (Remotes) to browse wrappers — paths can still be typed.</span>
          </div>

          <!-- Apps -->
          <div class="apps">
            <div v-for="(app, i) in sel.apps" :key="i" class="app">
              <div class="app-row">
                <span class="idx">#{{ i }}</span>
                <input v-model="app.path" class="grow" placeholder="wrapper path, e.g. blink/a2a_comm_only.py" />
                <button
                  class="btn browse"
                  :disabled="!sourceCluster"
                  :title="sourceCluster ? `Browse wrappers on ${sourceCluster}` : 'Connect a cluster to browse wrappers'"
                  @click="openWrapperPicker(i)"
                >
                  Browse…
                </button>
                <select
                  class="role"
                  :value="app.collect ? 'victim' : 'aggressor'"
                  @change="app.collect = ($event.target as HTMLSelectElement).value === 'victim'"
                >
                  <option value="victim">victim (measured)</option>
                  <option value="aggressor">aggressor</option>
                </select>
                <select v-if="groupNames.length" v-model="app.partition" class="role" title="Node group">
                  <option value="">— no group —</option>
                  <option v-for="g in groupNames" :key="g" :value="g">{{ g }}</option>
                </select>
                <button class="icon-btn danger" title="Remove app" @click="removeApp(i)">
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M5 7h14" /><path d="M9 7V5h6v2" /><path d="M7 7l1 13h8l1-13" />
                  </svg>
                </button>
              </div>
              <input v-model="app.args" class="full" placeholder="args, e.g. -msgsize 8192 -iter 1000" />
              <div class="timing">
                <label>Starts
                  <select v-model="app.startKind">
                    <option value="at_start">at start</option>
                    <option value="delay">after a delay</option>
                    <option value="after">after another app</option>
                  </select>
                </label>
                <label v-if="app.startKind === 'delay'">Delay (s) <input v-model="app.startDelay" /></label>
                <label v-if="app.startKind === 'after'">After
                  <select v-model="app.startAfter">
                    <option v-for="j in otherIndices(i)" :key="j" :value="String(j)">#{{ j }}</option>
                  </select>
                </label>
                <label>Ends
                  <select v-model="app.endKind">
                    <option value="complete">runs to completion</option>
                    <option value="force">stops when the others finish</option>
                    <option value="timed">stops after N seconds</option>
                  </select>
                </label>
                <label v-if="app.endKind === 'timed'">Seconds <input v-model="app.endTimed" /></label>
              </div>
            </div>
            <button class="btn" @click="addApp">+ Add app</button>
          </div>

          <!-- Per-experiment overrides (local_options) -->
          <div class="section overrides">
            <button class="sec-head" @click="showOverrides = !showOverrides">
              <span>Overrides for this experiment</span>
              <span class="sec-state">
                <span v-if="overridesActive" class="dot" />
                <span class="caret">{{ showOverrides ? "▾" : "▸" }}</span>
              </span>
            </button>
            <div v-show="showOverrides" class="ov-body">
              <p class="hint">Unset fields inherit the use-case globals. Set one only to vary it for this experiment.</p>
              <label class="ov-toggle">
                <input type="checkbox" v-model="sel.overrideAlloc" />
                Override node allocation for this experiment
              </label>
              <AllocationEditor v-if="sel.overrideAlloc" :alloc="sel.allocation" />
              <OptionsFields :options="sel.options" unset-label="inherit" />
            </div>
          </div>

          <button class="btn danger remove-exp" @click="removeExperiment">Remove experiment</button>
        </template>
        <p v-else class="empty pad">Select or add an experiment to edit its apps.</p>
      </main>

      <!-- JSON view -->
      <aside v-if="showJson" class="jsonpane">
        <header>config.json</header>
        <pre>{{ store.configJson }}</pre>
      </aside>
    </div>

    <!-- Open overlay: searchable library picker -->
    <div v-if="showOpen" class="modal-bg" @click.self="showOpen = false">
      <div class="modal card open-modal">
        <input
          v-model="openQuery"
          class="search"
          placeholder="Search saved use cases…"
          autofocus
        />
        <ul class="open-list">
          <li
            v-for="e in filteredLibrary"
            :key="e.id"
            :class="{ current: e.id === store.entryId }"
            @click="openEntry(e.id)"
          >
            <span class="open-name">{{ e.name }}</span>
            <span class="open-meta">
              {{ Object.keys(e.config.experiments || {}).length }} exp ·
              {{ e.updated_at.slice(0, 10) }}
            </span>
          </li>
          <li v-if="!filteredLibrary.length" class="empty">No matching use cases.</li>
        </ul>
      </div>
    </div>

    <!-- Wrapper picker overlay: searchable catalog from the source cluster -->
    <div v-if="showWrapper" class="modal-bg" @click.self="showWrapper = false">
      <div class="modal card wrapper-modal">
        <header class="wm-head">
          <input v-model="wrapperQuery" class="search" placeholder="Search wrappers by name, path, or group…" autofocus />
          <span class="wm-src">{{ sourceCluster }}</span>
        </header>

        <p v-if="catalog.busy[sourceCluster]" class="wm-state">Loading wrappers from {{ sourceCluster }}…</p>
        <p v-else-if="catalog.error[sourceCluster]" class="wm-state err">{{ catalog.error[sourceCluster] }}</p>

        <div v-else class="wrapper-list">
          <template v-for="[group, items] in wrapperGroups" :key="group">
            <div class="wg-head">{{ group }}</div>
            <button
              v-for="w in items"
              :key="w.relpath"
              class="wrap-row"
              :class="{ unloadable: !w.loadable }"
              :title="w.error || w.relpath"
              @click="chooseWrapper(w.relpath)"
            >
              <span class="wrap-main">
                <span class="wrap-name">{{ w.bench_name || w.file }}</span>
                <span class="wrap-path">{{ w.relpath }}</span>
              </span>
              <span class="wrap-tags">
                <span v-if="w.metadata.length" class="tag">{{ w.metadata.length }} metric{{ w.metadata.length === 1 ? "" : "s" }}</span>
                <span v-if="!w.loadable" class="tag warn">unloadable</span>
              </span>
            </button>
          </template>
          <p v-if="!wrapperGroups.length" class="empty">No matching wrappers.</p>
        </div>
      </div>
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
.btn.browse { padding: 0.35rem 0.6rem; white-space: nowrap; }

/* Cluster-node reference (informational) */
.nodes-ref { margin: -0.2rem 0 0.4rem; }
.link-btn { background: transparent; border: none; color: var(--text2); cursor: pointer;
  font-family: var(--mono); font-size: 0.72rem; padding: 0; }
.link-btn:hover { color: var(--text); }
.nodes-body { margin-top: 0.3rem; }
.nodes-body .hint { color: var(--text3); font-size: 0.72rem; margin-bottom: 0.2rem; }
.nodes-body .hint.err { color: var(--danger); }
.nodes-body .hint.muted { color: var(--text3); font-style: italic; }
.part-list { list-style: none; max-height: 9rem; overflow-y: auto; display: flex;
  flex-direction: column; gap: 0.1rem; margin: 0.2rem 0; }
.part-list li { display: flex; justify-content: space-between; gap: 0.5rem;
  font-size: 0.72rem; color: var(--text2); padding: 0.1rem 0.2rem; }
.pname { font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pmeta { color: var(--text3); white-space: nowrap; }

/* Wrapper source line + picker */
.wrapper-source { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem; }
.wrapper-source label { display: flex; align-items: center; gap: 0.4rem; color: var(--text2); font-size: 0.78rem; }
.src-note { color: var(--text2); font-size: 0.78rem; }
.src-note.muted { color: var(--text3); }
.src-note b { color: var(--text); }
.wrapper-modal { width: min(44rem, 94vw); padding: 0.75rem; display: flex; flex-direction: column; }
.wm-head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; }
.wm-head .search { flex: 1; margin-bottom: 0; }
.wm-src { font-size: 0.72rem; color: var(--text3); border: 1px solid var(--border);
  border-radius: 999px; padding: 0.1rem 0.5rem; white-space: nowrap; }
.wm-state { color: var(--text2); font-size: 0.82rem; padding: 1rem 0.5rem; }
.wm-state.err { color: var(--danger); }
.wrapper-list { max-height: 26rem; overflow-y: auto; display: flex; flex-direction: column; gap: 0.1rem; }
.wg-head { position: sticky; top: 0; background: var(--bg1); color: var(--text3);
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  padding: 0.4rem 0.55rem 0.2rem; }
.wrap-row { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem;
  width: 100%; text-align: left; background: transparent; border: 1px solid transparent;
  border-radius: var(--r); padding: 0.4rem 0.55rem; cursor: pointer; color: var(--text); }
.wrap-row:hover { background: var(--bg2); border-color: var(--accent); }
.wrap-row.unloadable { opacity: 0.7; }
.wrap-main { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }
.wrap-name { font-size: 0.84rem; }
.wrap-path { font-size: 0.7rem; color: var(--text3); font-family: var(--mono); }
.wrap-tags { display: flex; gap: 0.25rem; flex-shrink: 0; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
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
/* Collapsible globals section (allocation, later convergence/output/advanced) */
.section { margin-top: 0.8rem; padding-top: 0.8rem; border-top: 1px solid var(--border); }
.sec-head { width: 100%; display: flex; align-items: center; justify-content: space-between;
  background: transparent; border: none; cursor: pointer; padding: 0; color: var(--text2);
  font-family: var(--mono); font-size: 0.8rem; margin-bottom: 0.5rem; }
.sec-head:hover { color: var(--text); }
.sec-state { display: flex; align-items: center; gap: 0.4rem; }
.sec-head .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.sec-head .caret { color: var(--text3); }

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
/* Abstract flow diagram */
.flow { display: flex; align-items: stretch; gap: 0.5rem; overflow-x: auto;
  border: 1px solid var(--border); border-radius: var(--r); padding: 0.75rem; background: var(--bg2); }
.flow-col { display: flex; flex-direction: column; gap: 0.5rem; justify-content: center; }
.flow-arrow { display: flex; align-items: center; color: var(--text3); font-size: 1rem; }
.node { min-width: 9rem; border-radius: var(--r); padding: 0.45rem 0.6rem;
  border: 1px solid var(--border2); display: flex; flex-direction: column; gap: 0.25rem; }
.node.victim { border-left: 3px solid var(--accent); background: var(--accent-glow); }
.node.aggressor { border-left: 3px solid var(--warn); }
.node-name { font-size: 0.82rem; color: var(--text); }
.node-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.role-tag { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--text2); }
.tag { font-size: 0.62rem; color: var(--text3); border: 1px solid var(--border);
  border-radius: 999px; padding: 0 0.4rem; }
.role { font-size: 0.78rem; }

/* Apps */
.apps { display: flex; flex-direction: column; gap: 0.6rem; }
.app { border: 1px solid var(--border); border-radius: var(--r); padding: 0.6rem;
  display: flex; flex-direction: column; gap: 0.5rem; background: var(--bg2); }
.app-row { display: flex; align-items: center; gap: 0.5rem; }
.idx { color: var(--text3); font-size: 0.75rem; }
.grow { flex: 1; }
.full { width: 100%; }
.chk { display: flex; align-items: center; gap: 0.3rem; color: var(--text2); font-size: 0.78rem;
  white-space: nowrap; flex-direction: row; }
.chk input { accent-color: var(--accent); }
.timing { display: flex; flex-wrap: wrap; gap: 0.5rem 0.75rem; align-items: end; }
.timing label { flex-direction: column; }
.remove-exp { align-self: flex-start; }

/* Per-experiment overrides */
.overrides { margin-top: 0; }
.ov-body { display: flex; flex-direction: column; gap: 0.8rem; }
.ov-body .hint { color: var(--text3); font-size: 0.75rem; }
.ov-toggle { display: flex; align-items: center; gap: 0.4rem; color: var(--text2); font-size: 0.8rem; cursor: pointer; }
.ov-toggle input { accent-color: var(--accent); }

.jsonpane { width: 26rem; max-height: 36rem; overflow: auto; }
.jsonpane header { position: sticky; top: 0; background: var(--bg2); color: var(--text2);
  padding: 0.4rem 0.75rem; font-size: 0.75rem; border-bottom: 1px solid var(--border); }
.jsonpane pre { padding: 0.75rem; font-size: 0.75rem; white-space: pre-wrap; color: var(--text2); }

.banner { padding: 0.5rem 0.75rem; border-radius: var(--r); margin-bottom: 1rem; }
.banner.err { background: rgba(245, 101, 101, 0.12); color: var(--danger); border: 1px solid var(--danger); }

.validity { margin-bottom: 1rem; font-size: 0.8rem; }
.validity .vok { color: var(--ok); }
.vbtn { background: transparent; border: none; color: var(--warn); cursor: pointer;
  font-family: var(--mono); font-size: 0.8rem; padding: 0; }
.caret { color: var(--text3); }
.issue-list { list-style: none; margin-top: 0.4rem; padding-left: 0.5rem;
  border-left: 2px solid var(--warn); color: var(--text2); display: flex;
  flex-direction: column; gap: 0.15rem; }

.modal-bg { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { width: min(40rem, 92vw); padding: 1.25rem; }
.modal h2 { font-family: var(--sans); font-size: 1.1rem; margin-bottom: 0.3rem; }
.modal .hint { color: var(--text2); font-size: 0.8rem; margin-bottom: 0.75rem; }
.modal textarea { width: 100%; resize: vertical; }
.modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.75rem; }
.card { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r2); }

/* Open overlay */
.open-modal { width: min(34rem, 92vw); padding: 0.75rem; }
.search { width: 100%; margin-bottom: 0.6rem; font-size: 0.9rem; padding: 0.5rem 0.6rem; }
.open-list { list-style: none; max-height: 24rem; overflow-y: auto; display: flex;
  flex-direction: column; gap: 0.2rem; }
.open-list li { display: flex; justify-content: space-between; align-items: center;
  padding: 0.45rem 0.55rem; border: 1px solid transparent; border-radius: var(--r); cursor: pointer; }
.open-list li:hover { background: var(--bg2); }
.open-list li.current { border-color: var(--accent); }
.open-name { color: var(--text); }
.open-meta { color: var(--text3); font-size: 0.72rem; }
</style>
