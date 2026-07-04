<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from "vue";
import { useAuthorStore } from "@/stores/author";
import { useRemotesStore } from "@/stores/remotes";
import { useCatalogStore } from "@/stores/catalog";
import type { Wrapper } from "@/api/types";
import {
  cloneAllocation,
  cloneExperiment,
  emptyApp,
  emptyExperiment,
  flowForest,
  hasAllocation,
  validateDraft,
} from "@/lib/config";
import { equalShares, sliceColor, sliceName } from "@/lib/slices";
import AllocationEditor from "@/components/AllocationEditor.vue";
import OptionsFields from "@/components/OptionsFields.vue";
import SbatchEditor from "@/components/SbatchEditor.vue";
import FlowChain from "@/components/FlowChain.vue";
import ConfirmButton from "@/components/ConfirmButton.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import LibraryBar from "@/components/author/LibraryBar.vue";
import AuthorRail from "@/components/author/AuthorRail.vue";

const store = useAuthorStore();
const remotes = useRemotesStore();
const catalog = useCatalogStore();
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
const showNodes = ref(false);
const nodesInfo = computed(() => catalog.nodes[sourceCluster.value]);
function toggleNodes() {
  showNodes.value = !showNodes.value;
  if (showNodes.value && sourceCluster.value) catalog.loadNodes(sourceCluster.value);
}

const flow = computed(() =>
  sel.value ? flowForest(sel.value.apps, effectiveAlloc.value, d.numnodes) : [],
);
const issues = computed(() => validateDraft(d));
const showIssues = ref(false);
const showOverrides = ref(false);

// Node groups an app may target = the EFFECTIVE allocation for the selected
// experiment (its local override when set, otherwise the global allocation).
const effectiveAlloc = computed(() => {
  const e = sel.value;
  return e && e.overrideAlloc ? e.allocation : d.allocation;
});

// Every partition of the effective allocation, paired with a colour (colour
// index = position in the full partitions list, matching AllocationEditor) and
// a display name that falls back to the same "group N" label AllocationEditor
// shows for an unnamed slice, so a still-unnamed 3rd+ slice (an edge case:
// fresh slices are auto-named on creation, see AllocationEditor.vue) is still
// visible and selectable here instead of silently disappearing.
const namedSlices = computed(() =>
  effectiveAlloc.value.by === "groups"
    ? effectiveAlloc.value.partitions.map((p, i) => ({
        name: sliceName(p.name, i),
        share: p.share,
        color: sliceColor(i),
      }))
    : [],
);
const groupNames = computed(() => namedSlices.value.map((s) => s.name));
function colorForGroup(name: string): string {
  return namedSlices.value.find((s) => s.name === name)?.color ?? "var(--text3)";
}
// Mini placement-bar widths (%), mirroring AllocationEditor's even-split rule:
// no explicit shares -> equal division across slices; otherwise each slice's
// own share (a display approximation, not the exact runtime placement).
const miniSlices = computed(() => {
  const ps = namedSlices.value;
  const n = ps.length;
  if (!n) return [{ color: "var(--accent)", width: 100 }];
  const anySet = ps.some((p) => p.share.trim() !== "");
  const widths = anySet
    ? ps.map((p) => {
        const v = parseInt(p.share.trim(), 10);
        return Number.isFinite(v) ? v : Math.round(100 / n);
      })
    : equalShares(n);
  return ps.map((p, i) => ({ color: p.color, width: widths[i] }));
});

// "active" for the run-settings card only — placement override has its own
// inherited/override badge on the placement summary card above.
const overridesActive = computed(() => {
  const e = sel.value;
  if (!e) return false;
  return Object.values(e.options).some((v) => v !== "");
});

function addApp() {
  sel.value?.apps.push(emptyApp());
}
function removeApp(i: number) {
  sel.value?.apps.splice(i, 1);
}
function toggleOverride(): void {
  const e = sel.value;
  if (!e) return;
  if (!e.overrideAlloc && !hasAllocation(e.allocation)) {
    // Fork from the current global allocation instead of starting blank, so
    // the editor opens pre-filled with what the user already sees globally.
    // This is a one-time copy, not a live binding: later edits to the global
    // allocation never retroactively change an override that already exists.
    e.allocation = cloneAllocation(d.allocation);
  }
  e.overrideAlloc = !e.overrideAlloc;
}
function otherIndices(self: number): number[] {
  return (sel.value?.apps ?? []).map((_, j) => j).filter((j) => j !== self);
}

// -- Wrapper picker (searchable overlay over the cluster catalog) ----------
const showWrapper = ref(false);
const wrapperFor = ref<number | null>(null);
const wrapperQuery = ref("");
const collapsedSuites = ref<Set<string>>(new Set());
function toggleSuite(group: string): void {
  const next = new Set(collapsedSuites.value);
  if (next.has(group)) next.delete(group);
  else next.add(group);
  collapsedSuites.value = next;
}
// A suite stays open while searching, so a match is never hidden inside a
// collapsed suite the user forgot about.
function isSuiteOpen(group: string): boolean {
  return wrapperQuery.value.trim() !== "" || !collapsedSuites.value.has(group);
}

type WrapperOrigin = "host" | "remote" | "both";
type TaggedWrapper = Wrapper & { origin: WrapperOrigin };

function openWrapperPicker(appIndex: number) {
  wrapperFor.value = appIndex;
  wrapperQuery.value = "";
  showWrapper.value = true;
  // The host catalog always loads (no connection needed); the remote catalog
  // needs a connected cluster, without one the picker still opens so the
  // user can force a free path via "+ Add" or pick a host-only wrapper.
  catalog.loadLocalBenchmarks();
  if (sourceCluster.value) catalog.loadBenchmarks(sourceCluster.value);
}
const localWrappers = computed(() => catalog.localBenchmarks?.wrappers ?? []);
const remoteWrappers = computed(() => catalog.benchmarks[sourceCluster.value]?.wrappers ?? []);
const wrapperCatalogBusy = computed(
  () => catalog.localBusy || (!!sourceCluster.value && !!catalog.busy[sourceCluster.value]),
);
// Merged by relpath: present on the host, the remote, or both. Matching by
// relpath assumes both sides are the same crab repo checkout (possibly a
// different branch/commit), a reasonable identity key for a wrapper file.
const wrappers = computed<TaggedWrapper[]>(() => {
  const byPath = new Map<string, TaggedWrapper>();
  for (const w of localWrappers.value) byPath.set(w.relpath, { ...w, origin: "host" });
  for (const w of remoteWrappers.value) {
    const onHost = byPath.has(w.relpath);
    byPath.set(w.relpath, { ...w, origin: onHost ? "both" : "remote" });
  }
  return [...byPath.values()];
});
function originOf(relpath: string): WrapperOrigin | null {
  return wrappers.value.find((w) => w.relpath === relpath)?.origin ?? null;
}
/** Non-blocking heads-up when a chosen wrapper isn't on the currently targeted remote. */
function wrapperWarning(relpath: string): string {
  if (!relpath.trim() || !sourceCluster.value) return "";
  if (originOf(relpath) === "host") {
    return `Not found on ${sourceCluster.value}, running there may fail unless this wrapper is synced.`;
  }
  return "";
}
const filteredWrappers = computed(() => {
  const q = wrapperQuery.value.trim().toLowerCase();
  const list = wrappers.value;
  if (!q) return list;
  return list.filter((w) =>
    [w.relpath, w.bench_name, w.benchmark_id, w.group].some((s) => s?.toLowerCase().includes(q)),
  );
});
// Group by suite (top folder); within a suite, sort by the rest of the path so
// benchmark/version order is preserved.
const wrapperGroups = computed(() => {
  const by: Record<string, typeof filteredWrappers.value> = {};
  for (const w of filteredWrappers.value) (by[w.group || "other"] ??= []).push(w);
  for (const items of Object.values(by)) items.sort((a, b) => a.relpath.localeCompare(b.relpath));
  return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
});
// The path within its suite, e.g. "blink/v7/a2a.py" → "v7/a2a.py".
function subPath(relpath: string, group: string): string {
  return group && relpath.startsWith(group + "/") ? relpath.slice(group.length + 1) : relpath;
}
// Offer "+ Add" when the typed query isn't already an exact wrapper path.
const trimmedQuery = computed(() => wrapperQuery.value.trim());
const canAddFree = computed(
  () => !!trimmedQuery.value && !wrappers.value.some((w) => w.relpath === trimmedQuery.value),
);
function chooseWrapper(relpath: string) {
  const e = sel.value;
  if (e && wrapperFor.value !== null && e.apps[wrapperFor.value]) {
    e.apps[wrapperFor.value].path = relpath;
  }
  showWrapper.value = false;
  wrapperFor.value = null;
}

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
          <h2 class="pane-title">Basics</h2>
          <div class="job-grid">
            <label class="wide"
              >Name <input v-model="d.name" />
              <small>Names the run and prefixes the output folder.</small>
            </label>
            <label
              >Nodes <input v-model="d.numnodes" />
              <small>Total nodes to allocate.</small>
            </label>
            <label
              >Processes per node <input v-model="d.ppn" />
              <small>Tasks launched per node (ppn).</small>
            </label>
          </div>

          <!-- Informational cluster-node reference for sizing `Nodes` -->
          <div v-if="sourceCluster" class="nodes-ref">
            <button class="link-btn" @click="toggleNodes">
              {{ showNodes ? "▾" : "▸" }} cluster nodes · {{ sourceCluster }}
            </button>
            <div v-if="showNodes" class="nodes-body">
              <p v-if="catalog.busy[sourceCluster]" class="hint">Loading…</p>
              <p v-else-if="catalog.error[sourceCluster]" class="hint err">
                {{ catalog.error[sourceCluster] }}
              </p>
              <template v-else-if="nodesInfo">
                <p v-if="!nodesInfo.available" class="hint">
                  {{ nodesInfo.note || "sinfo unavailable here" }}
                </p>
                <template v-else>
                  <p class="hint">
                    {{ nodesInfo.nodes.length }} nodes ·
                    {{ nodesInfo.partitions.length }} partition{{
                      nodesInfo.partitions.length === 1 ? "" : "s"
                    }}
                  </p>
                  <ul class="part-list">
                    <li v-for="p in nodesInfo.partitions" :key="p.name">
                      <span class="pname">{{ p.name }}</span>
                      <span class="pmeta"
                        >{{ p.nodes ?? "?" }}<span v-if="p.avail"> · {{ p.avail }}</span></span
                      >
                    </li>
                  </ul>
                  <p class="hint muted">
                    Slurm partitions on the cluster, not the same as allocation node groups.
                  </p>
                </template>
              </template>
            </div>
          </div>
        </template>

        <!-- GLOBAL · Node allocation -->
        <template v-else-if="view.kind === 'global' && view.id === 'alloc'">
          <h2 class="pane-title">Node allocation</h2>
          <AllocationEditor :alloc="d.allocation" :numnodes="d.numnodes" />
        </template>

        <!-- GLOBAL · Run settings (convergence, output & advanced, slurm) -->
        <template v-else-if="view.kind === 'global' && view.id === 'run'">
          <h2 class="pane-title">Run settings</h2>
          <OptionsFields :options="d.options" />
          <h3 class="section-title">Slurm directives</h3>
          <SbatchEditor :sbatch="d.sbatch" />
        </template>

        <!-- EXPERIMENT -->
        <template v-else-if="sel">
          <div class="exp-edit">
            <label>Experiment name <input v-model="sel.name" /></label>
            <label
              >Description <input v-model="sel.description" placeholder="optional note"
            /></label>
          </div>

          <!-- Placement: the node allocation this experiment's apps attach to,
               inherited from the global allocation unless overridden here. -->
          <div class="place-section">
            <div class="seclabel">Placement</div>
            <div class="place">
              <span class="mini">
                <i
                  v-for="(m, mi) in miniSlices"
                  :key="mi"
                  :style="{ background: m.color, width: m.width + '%' }"
                />
              </span>
              <span v-if="groupNames.length" class="txt">
                Uses {{ sel.overrideAlloc ? "its own" : "the global" }} allocation:
                <b>{{ groupNames.join(" / ") }}</b
                >, laid out <b>{{ effectiveAlloc.mode }}</b>
              </span>
              <span v-else class="txt">The machine runs one workload, no division.</span>
              <span class="inh" :class="{ over: sel.overrideAlloc }">{{
                sel.overrideAlloc ? "override" : "inherited"
              }}</span>
              <span class="spacer" />
              <button class="btn" @click="toggleOverride">
                {{ sel.overrideAlloc ? "Use global allocation" : "Override for this experiment" }}
              </button>
            </div>
            <AllocationEditor
              v-if="sel.overrideAlloc"
              :alloc="sel.allocation"
              :numnodes="d.numnodes"
            />
          </div>

          <!-- Per-experiment run-setting overrides (local_options) — expandable card -->
          <div class="ov-card" :class="{ open: showOverrides }">
            <button class="ov-head" @click="showOverrides = !showOverrides">
              <svg
                class="chev"
                :class="{ open: showOverrides }"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
              <span class="ov-title">Override run settings</span>
              <span v-if="overridesActive" class="ov-badge">active</span>
              <span class="ov-hint">{{
                showOverrides ? "" : "change settings for this experiment only"
              }}</span>
            </button>
            <div v-show="showOverrides" class="ov-body">
              <p class="hint">
                Leave a field on <em>inherit</em> or blank to use the global run settings. Set one
                only to change it for this experiment.
              </p>
              <OptionsFields :options="sel.options" unset-label="inherit" />
            </div>
          </div>

          <!-- App flow: a dependency tree. Roots run together; an arrow points
               from an app to each app that starts after it. Colour = allocation group. -->
          <div v-if="sel.apps.length" class="flow">
            <div class="flow-roots">
              <FlowChain v-for="root in flow" :key="root.index" :node="root" />
            </div>
          </div>

          <!-- Where the wrapper picker sources its catalog -->
          <div class="wrapper-source">
            <template v-if="connectedClusters.length > 1">
              <label
                >Wrappers from
                <select v-model="sourceCluster">
                  <option v-for="c in connectedClusters" :key="c" :value="c">{{ c }}</option>
                </select>
              </label>
            </template>
            <span v-else-if="sourceCluster" class="src-note"
              >Wrappers from <b>{{ sourceCluster }}</b></span
            >
            <span v-else class="src-note muted"
              >Connect a cluster in Remotes to browse wrappers. You can still type a path.</span
            >
          </div>

          <!-- Apps -->
          <div class="apps">
            <div v-for="(app, i) in sel.apps" :key="i" class="app">
              <div class="app-row">
                <span class="idx">#{{ i }}</span>
                <button
                  class="wrap-chip"
                  :class="{ empty: !app.path }"
                  title="Choose or change the wrapper"
                  @click="openWrapperPicker(i)"
                >
                  <span class="chip-text">{{ app.path || "Choose wrapper…" }}</span>
                  <svg class="chip-ic" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                </button>
                <span
                  v-if="wrapperWarning(app.path)"
                  class="wrap-warn"
                  :title="wrapperWarning(app.path)"
                  >&#9888;</span
                >
                <span v-if="groupNames.length" class="slicepick" title="Node group">
                  <span
                    class="sw"
                    :style="{
                      background: app.partition ? colorForGroup(app.partition) : 'var(--text3)',
                    }"
                  />
                  <select v-model="app.partition">
                    <option value="">no group</option>
                    <option v-for="g in groupNames" :key="g" :value="g">{{ g }}</option>
                  </select>
                </span>
                <button
                  type="button"
                  class="toggle"
                  role="switch"
                  :aria-checked="app.collect"
                  title="Parse and store this app's metrics"
                  @click="app.collect = !app.collect"
                >
                  <span class="swi" :class="{ off: !app.collect }" />
                  collect metrics
                </button>
                <ConfirmButton
                  v-slot="{ trigger }"
                  :label="app.path || `app #${i}`"
                  @confirm="removeApp(i)"
                >
                  <button type="button" class="icon-btn danger" title="Remove app" @click="trigger">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M5 7h14" />
                      <path d="M9 7V5h6v2" />
                      <path d="M7 7l1 13h8l1-13" />
                    </svg>
                  </button>
                </ConfirmButton>
              </div>
              <input v-model="app.args" class="full" placeholder="args" />
              <div class="timing">
                <label
                  >Starts
                  <select v-model="app.startKind">
                    <option value="at_start">at start</option>
                    <option value="delay">after a delay</option>
                    <option value="after">after another app</option>
                  </select>
                </label>
                <label v-if="app.startKind === 'delay'"
                  >Delay (s) <input v-model="app.startDelay"
                /></label>
                <label v-if="app.startKind === 'after'"
                  >After
                  <select v-model="app.startAfter">
                    <option v-for="j in otherIndices(i)" :key="j" :value="String(j)">
                      #{{ j }}
                    </option>
                  </select>
                </label>
                <label
                  >Ends
                  <select v-model="app.endKind">
                    <option value="complete">runs to completion</option>
                    <option value="force">stops when the others finish</option>
                    <option value="timed">stops after N seconds</option>
                  </select>
                </label>
                <label v-if="app.endKind === 'timed'"
                  >Seconds <input v-model="app.endTimed"
                /></label>
              </div>
            </div>
            <button class="btn" @click="addApp">+ Add app</button>
          </div>

          <ConfirmButton
            v-if="curExpIndex !== null"
            v-slot="{ trigger }"
            class="remove-exp"
            :label="sel.name || 'this experiment'"
            @confirm="removeExperiment(curExpIndex!)"
          >
            <button class="btn danger" @click="trigger">Remove experiment</button>
          </ConfirmButton>
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

    <!-- Wrapper picker overlay: searchable catalog from the source cluster -->
    <div v-if="showWrapper" class="modal-bg" @click.self="showWrapper = false">
      <div class="modal card wrapper-modal">
        <header class="wm-head">
          <input
            v-model="wrapperQuery"
            class="search"
            placeholder="Search wrappers by name, path, or group…"
            autofocus
          />
          <span class="wm-src">{{ sourceCluster }}</span>
        </header>

        <p v-if="wrapperCatalogBusy" class="wm-state">Loading wrappers…</p>

        <div v-else class="wrapper-list">
          <p v-if="catalog.localError" class="wm-hint err">
            Host wrappers: {{ catalog.localError }}
          </p>
          <p v-if="sourceCluster && catalog.error[sourceCluster]" class="wm-hint err">
            {{ sourceCluster }}: {{ catalog.error[sourceCluster] }}
            <button class="btn" @click="catalog.loadBenchmarks(sourceCluster, true)">Retry</button>
          </p>
          <p v-if="!sourceCluster && !catalog.localError" class="wm-hint">
            No cluster connected, showing wrappers on this host only.
          </p>
          <template v-for="[group, items] in wrapperGroups" :key="group">
            <button type="button" class="wg-head" @click="toggleSuite(group)">
              <svg
                class="chev"
                :class="{ open: isSuiteOpen(group) }"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
              <span class="wg-title">{{ group }}</span>
            </button>
            <button
              v-for="w in items"
              v-show="isSuiteOpen(group)"
              :key="w.relpath"
              class="wrap-row"
              :class="{ unloadable: !w.loadable }"
              :title="w.error || w.relpath"
              @click="chooseWrapper(w.relpath)"
            >
              <span class="wrap-main">
                <span class="wrap-name">{{ w.bench_name || w.file }}</span>
                <span class="wrap-path">{{ subPath(w.relpath, group) }}</span>
              </span>
              <span class="wrap-tags">
                <span class="tag origin" :class="w.origin">{{
                  w.origin === "both" ? "host + remote" : w.origin
                }}</span>
                <span v-if="w.metadata.length" class="tag"
                  >{{ w.metadata.length }} metric{{ w.metadata.length === 1 ? "" : "s" }}</span
                >
                <span
                  v-if="!w.loadable"
                  class="tag warn"
                  title="Introspection failed. The path still works."
                  >unloadable</span
                >
              </span>
            </button>
          </template>
          <p v-if="!wrapperGroups.length && !canAddFree" class="empty">
            Type a wrapper path to add it.
          </p>
        </div>

        <!-- Force a path that isn't in the catalog (e.g. not on the remote yet) -->
        <button v-if="canAddFree" class="wm-add" @click="chooseWrapper(trimmedQuery)">
          + Add "{{ trimmedQuery }}"
          <span class="wm-add-hint"
            >use this path even if it's not on {{ sourceCluster || "the cluster" }} yet</span
          >
        </button>
      </div>
    </div>
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
.btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.btn.danger:hover:not(:disabled) {
  background: var(--danger);
  color: var(--text);
}
.btn.on {
  border-color: var(--accent);
  color: var(--accent);
}
.btn.browse {
  padding: 0.35rem 0.6rem;
  white-space: nowrap;
}
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text2);
  border-radius: var(--r);
  padding: 0.2rem;
  cursor: pointer;
}
.icon-btn:hover {
  border-color: var(--border);
  color: var(--text);
}
.icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}

input,
textarea,
select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
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
.pane-title {
  font-family: var(--sans);
  font-size: var(--t-lg);
  color: var(--text);
}
.section-title {
  font-family: var(--sans);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text3);
  margin-top: -0.3rem;
}
.job-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.9rem;
  max-width: 38rem;
}
.job-grid label.wide {
  grid-column: 1 / -1;
}
.job-grid label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: var(--t-md);
}
.job-grid small {
  color: var(--text3);
  font-size: var(--t-xs);
  line-height: 1.3;
}
.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
.empty.pad {
  padding: 2rem;
}

/* Cluster-node reference (informational) */
.nodes-ref {
  margin-top: -0.2rem;
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
.nodes-body {
  margin-top: 0.4rem;
  max-width: 30rem;
}
.nodes-body .hint {
  color: var(--text3);
  font-size: var(--t-sm);
  margin-bottom: 0.2rem;
}
.nodes-body .hint.err {
  color: var(--danger);
}
.nodes-body .hint.muted {
  color: var(--text3);
  font-style: italic;
}
.part-list {
  list-style: none;
  max-height: 12rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  margin: 0.2rem 0;
}
.part-list li {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: var(--t-sm);
  color: var(--text2);
  padding: 0.12rem 0.2rem;
}
.pname {
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pmeta {
  color: var(--text3);
  white-space: nowrap;
}

/* Experiment editor */
.exp-edit {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  max-width: 38rem;
}
.exp-edit label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: var(--t-md);
}

/* App flow diagram (nodes + edges live in FlowChain.vue) */
.flow {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.75rem;
  background: var(--bg2);
}
.flow-roots {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  align-items: flex-start;
  width: max-content;
}
/* shared tag pill (wrapper picker rows) */
.tag {
  font-size: var(--t-xs);
  color: var(--text3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 0.4rem;
}
.tag.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.tag.origin.host {
  color: var(--text2);
  border-color: var(--border2);
}
.tag.origin.remote {
  color: var(--accent);
  border-color: var(--accent);
}
.tag.origin.both {
  color: var(--ok);
  border-color: var(--ok);
}

/* Wrapper source line */
.wrapper-source {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.wrapper-source label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--text2);
  font-size: var(--t-sm);
}
.src-note {
  color: var(--text2);
  font-size: var(--t-sm);
}
.src-note.muted {
  color: var(--text3);
}
.src-note b {
  color: var(--text);
}

/* Apps — clearly separated cards */
.apps {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.app {
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  background: var(--bg2);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}
.app-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.idx {
  color: var(--text3);
  font-size: var(--t-sm);
  font-weight: 600;
}
.full {
  width: 100%;
}

/* Wrapper chip (opens the picker) */
.wrap-chip {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.4rem 0.6rem;
  cursor: pointer;
  color: var(--text);
  font-family: var(--mono);
  font-size: var(--t-md);
  text-align: left;
}
.wrap-chip:hover {
  border-color: var(--accent);
}
.wrap-chip.empty .chip-text {
  color: var(--text3);
}
.chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip-ic {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--text3);
  flex-shrink: 0;
}
.wrap-warn {
  color: var(--warn);
  font-size: 0.9rem;
  cursor: help;
}
.timing {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  align-items: end;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--border);
}
.timing label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  color: var(--text2);
  font-size: var(--t-sm);
}
.remove-exp {
  align-self: flex-start;
}

/* Placement summary card (ported from the approved integration.html mockup) */
.place-section {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.seclabel {
  font-family: var(--sans);
  font-size: var(--t-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text3);
  font-weight: 700;
}
.place {
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg2);
  padding: 0.6rem 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  flex-wrap: wrap;
}
.place .mini {
  display: flex;
  height: 20px;
  width: 120px;
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.place .mini i {
  display: block;
  font-style: normal;
}
.place .txt {
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
}
.place .txt b {
  color: var(--text);
}
.place .inh {
  font-family: var(--sans);
  font-size: var(--t-xs);
  color: var(--text3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.05rem 0.45rem;
  white-space: nowrap;
}
.place .inh.over {
  color: var(--accent);
  border-color: var(--accent);
}
.place .spacer {
  flex: 1;
}

/* Per-app slice picker — a native select dressed as a small pill with a colour dot */
.slicepick {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.25rem 0.5rem;
}
.slicepick .sw {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  flex-shrink: 0;
}
.slicepick select {
  background: transparent;
  border: none;
  padding: 0;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
}
.slicepick select:focus {
  outline: none;
}

/* Collect-metrics toggle switch */
.toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
  padding: 0;
  white-space: nowrap;
}
.toggle .swi {
  width: 30px;
  height: 17px;
  border-radius: 999px;
  background: var(--accent);
  position: relative;
  flex-shrink: 0;
  transition: background 0.12s ease;
}
.toggle .swi.off {
  background: var(--bg3);
}
.toggle .swi::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 15px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  transition: left 0.12s ease;
}
.toggle .swi.off::after {
  left: 2px;
  background: var(--text3);
}

/* Per-experiment run-setting overrides — expandable card */
.ov-card {
  border: 1px solid var(--border);
  border-radius: var(--r2);
  background: var(--bg2);
}
.ov-card.open {
  border-color: var(--accent);
}
.ov-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0.7rem 0.9rem;
  color: var(--text);
  font-family: var(--sans);
  font-size: var(--t-md);
}
.ov-head:hover {
  background: var(--bg1);
  border-radius: var(--r2);
}
.chev {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--text3);
  transition: transform 0.12s ease;
}
.chev.open {
  transform: rotate(90deg);
  color: var(--accent);
}
.ov-title {
  font-weight: 600;
}
.ov-badge {
  font-size: var(--t-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 0 0.4rem;
}
.ov-hint {
  color: var(--text3);
  font-size: var(--t-sm);
  margin-left: auto;
}
.ov-body {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  padding: 0 0.9rem 0.9rem;
}
.ov-body .hint {
  color: var(--text3);
  font-size: var(--t-sm);
}
.ov-body .hint em {
  color: var(--text2);
  font-style: normal;
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

.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: min(40rem, 92vw);
  padding: 1.25rem;
}
.modal h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  margin-bottom: 0.3rem;
}
.modal .hint {
  color: var(--text2);
  font-size: var(--t-md);
  margin-bottom: 0.75rem;
}
.modal textarea {
  width: 100%;
  resize: vertical;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}

.search {
  width: 100%;
  margin-bottom: 0.6rem;
  font-size: var(--t-md);
  padding: 0.5rem 0.6rem;
}

/* Wrapper picker overlay */
.wrapper-modal {
  width: min(44rem, 94vw);
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
}
.wm-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}
.wm-head .search {
  flex: 1;
  margin-bottom: 0;
}
.wm-src {
  font-size: var(--t-sm);
  color: var(--text3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  white-space: nowrap;
}
.wm-state {
  color: var(--text2);
  font-size: var(--t-md);
  padding: 1rem 0.5rem;
}
.wm-hint {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 0.3rem 0.2rem;
}
.wm-hint.err {
  color: var(--danger);
}
.wrapper-list {
  max-height: 26rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
/* Prominent suite headers (wrappers/<suite>/…) */
.wg-head {
  position: sticky;
  top: 0;
  background: var(--bg1);
  color: var(--accent);
  font-family: var(--sans);
  font-size: var(--t-lg);
  font-weight: 700;
  letter-spacing: 0.01em;
  padding: 0.7rem 0.55rem 0.3rem;
  border: none;
  border-bottom: 1px solid var(--border);
  margin-bottom: 0.15rem;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.45rem;
  cursor: pointer;
  text-align: left;
}
.wg-head:not(:first-child) {
  margin-top: 0.5rem;
}
.wg-head:hover {
  background: var(--bg2);
}
.wg-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wrap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r);
  padding: 0.4rem 0.55rem;
  cursor: pointer;
  color: var(--text);
}
.wrap-row:hover {
  background: var(--bg2);
  border-color: var(--accent);
}
.wrap-row.unloadable {
  opacity: 0.7;
}
.wrap-main {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}
.wrap-name {
  font-size: var(--t-md);
}
.wrap-path {
  font-size: var(--t-sm);
  color: var(--text3);
  font-family: var(--mono);
}
.wrap-tags {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}
.wm-add {
  margin-top: 0.5rem;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  align-items: flex-start;
  background: var(--bg2);
  border: 1px dashed var(--accent);
  border-radius: var(--r);
  padding: 0.5rem 0.6rem;
  cursor: pointer;
  color: var(--accent);
  font-family: var(--mono);
  font-size: var(--t-md);
  text-align: left;
}
.wm-add:hover {
  background: var(--accent-glow);
}
.wm-add-hint {
  color: var(--text3);
  font-size: var(--t-sm);
}
</style>
