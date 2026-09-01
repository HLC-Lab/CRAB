<script setup lang="ts">
// EXPERIMENT: name/description, placement (global allocation or a per-
// experiment override), run-setting overrides, the app flow diagram, and the
// apps editor. Takes the experiment being edited plus the global allocation/
// numnodes it may inherit from as props (owned by the caller — the Author
// page's draft there, a SbatchMan campaign group's own draft elsewhere) so
// this component has no dependency on any particular store; `expIndex` is
// only carried through for the `remove-experiment` emit, `sourceCluster` is
// shared with the caller (Basics reads it, this pane is where it gets picked).
import { computed, ref } from "vue";
import { useRemotesStore } from "@/stores/remotes";
import { useCatalogStore } from "@/stores/catalog";
import type { Wrapper } from "@/api/types";
import {
  type AllocationDraft,
  type ExperimentDraft,
  cloneAllocation,
  emptyApp,
  flowForest,
  hasAllocation,
} from "@/lib/config";
import { equalShares, sliceColor, sliceName } from "@/lib/slices";
import AllocationEditor from "@/components/AllocationEditor.vue";
import OptionsFields from "@/components/OptionsFields.vue";
import FlowChain from "@/components/FlowChain.vue";
import ConfirmButton from "@/components/ConfirmButton.vue";
import AppCard from "@/components/author/AppCard.vue";
import WrapperPickerModal from "@/components/author/WrapperPickerModal.vue";

const props = defineProps<{
  experiment: ExperimentDraft;
  expIndex: number;
  globalAllocation: AllocationDraft;
  globalNumnodes: string;
  sourceCluster: string;
}>();
const emit = defineEmits<{
  "update:sourceCluster": [string];
  "remove-experiment": [number];
}>();

const remotes = useRemotesStore();
const catalog = useCatalogStore();

const sel = computed(() => props.experiment);

const connectedClusters = computed(() =>
  remotes.items.filter((r) => r.connected).map((r) => r.name),
);
const sourceClusterModel = computed({
  get: () => props.sourceCluster,
  set: (name: string) => emit("update:sourceCluster", name),
});

const showOverrides = ref(false);

// Node groups an app may target = the EFFECTIVE allocation for the selected
// experiment (its local override when set, otherwise the global allocation).
const effectiveAlloc = computed(() => {
  const e = sel.value;
  return e && e.overrideAlloc ? e.allocation : props.globalAllocation;
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

const flow = computed(() =>
  sel.value ? flowForest(sel.value.apps, effectiveAlloc.value, props.globalNumnodes) : [],
);

function toggleOverride(): void {
  const e = sel.value;
  if (!e) return;
  if (!e.overrideAlloc && !hasAllocation(e.allocation)) {
    // Fork from the current global allocation instead of starting blank, so
    // the editor opens pre-filled with what the user already sees globally.
    // This is a one-time copy, not a live binding: later edits to the global
    // allocation never retroactively change an override that already exists.
    e.allocation = cloneAllocation(props.globalAllocation);
  }
  e.overrideAlloc = !e.overrideAlloc;
}
function addApp() {
  sel.value?.apps.push(emptyApp());
}
function removeApp(i: number) {
  sel.value?.apps.splice(i, 1);
}
function otherIndices(self: number): number[] {
  return (sel.value?.apps ?? []).map((_, j) => j).filter((j) => j !== self);
}

// -- Wrapper picker (searchable overlay over the cluster catalog) ----------
const showWrapper = ref(false);
const wrapperFor = ref<number | null>(null);

type WrapperOrigin = "host" | "remote" | "both";
type TaggedWrapper = Wrapper & { origin: WrapperOrigin };

function openWrapperPicker(appIndex: number) {
  wrapperFor.value = appIndex;
  showWrapper.value = true;
  // The host catalog always loads (no connection needed); the remote catalog
  // needs a connected cluster, without one the picker still opens so the
  // user can force a free path via "+ Add" or pick a host-only wrapper.
  catalog.loadLocalBenchmarks();
  if (props.sourceCluster) catalog.loadBenchmarks(props.sourceCluster);
}
const localWrappers = computed(() => catalog.localBenchmarks?.wrappers ?? []);
const remoteWrappers = computed(() => catalog.benchmarks[props.sourceCluster]?.wrappers ?? []);
const wrapperCatalogBusy = computed(
  () => catalog.localBusy || (!!props.sourceCluster && !!catalog.busy[props.sourceCluster]),
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
  if (!relpath.trim() || !props.sourceCluster) return "";
  if (originOf(relpath) === "host") {
    return `Not found on ${props.sourceCluster}, running there may fail unless this wrapper is synced.`;
  }
  return "";
}
function chooseWrapper(relpath: string) {
  const e = sel.value;
  if (e && wrapperFor.value !== null && e.apps[wrapperFor.value]) {
    e.apps[wrapperFor.value].path = relpath;
  }
  showWrapper.value = false;
  wrapperFor.value = null;
}
</script>

<template>
  <div v-if="sel" class="exp-edit">
    <label>Experiment name <input v-model="sel.name" /></label>
    <label>Description <input v-model="sel.description" placeholder="optional note" /></label>
  </div>

  <!-- Placement: the node allocation this experiment's apps attach to,
       inherited from the global allocation unless overridden here. -->
  <div v-if="sel" class="place-section">
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
    <AllocationEditor v-if="sel.overrideAlloc" :alloc="sel.allocation" :numnodes="globalNumnodes" />
  </div>

  <!-- Per-experiment run-setting overrides (local_options) — expandable card -->
  <div v-if="sel" class="ov-card" :class="{ open: showOverrides }">
    <button class="ov-head" @click="showOverrides = !showOverrides">
      <svg class="chev" :class="{ open: showOverrides }" viewBox="0 0 24 24" aria-hidden="true">
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
        Leave a field on <em>inherit</em> or blank to use the global run settings. Set one only to
        change it for this experiment.
      </p>
      <OptionsFields :options="sel.options" unset-label="inherit" />
    </div>
  </div>

  <!-- App flow: a dependency tree. Roots run together; an arrow points
       from an app to each app that starts after it. Colour = allocation group. -->
  <div v-if="sel && sel.apps.length" class="flow">
    <div class="flow-roots">
      <FlowChain v-for="root in flow" :key="root.index" :node="root" />
    </div>
  </div>

  <!-- Where the wrapper picker sources its catalog -->
  <div v-if="sel" class="wrapper-source">
    <template v-if="connectedClusters.length > 1">
      <label
        >Wrappers from
        <select v-model="sourceClusterModel">
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
  <div v-if="sel" class="apps">
    <AppCard
      v-for="(app, i) in sel.apps"
      :key="i"
      :app="app"
      :index="i"
      :other-indices="otherIndices(i)"
      :group-names="groupNames"
      :color-for-group="colorForGroup"
      :wrapper-warning="wrapperWarning"
      @remove="removeApp(i)"
      @open-wrapper-picker="openWrapperPicker(i)"
    />
    <button class="btn" @click="addApp">+ Add app</button>
  </div>

  <ConfirmButton
    v-if="sel"
    v-slot="{ trigger }"
    class="remove-exp"
    :label="sel.name || 'this experiment'"
    @confirm="emit('remove-experiment', expIndex)"
  >
    <button class="btn danger" @click="trigger">Remove experiment</button>
  </ConfirmButton>

  <WrapperPickerModal
    v-if="showWrapper"
    :wrappers="wrappers"
    :source-cluster="sourceCluster"
    :busy="wrapperCatalogBusy"
    @choose="chooseWrapper"
    @close="showWrapper = false"
  />
</template>

<style scoped>
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
.btn.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.btn.danger:hover:not(:disabled) {
  background: var(--danger);
  color: var(--text);
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
</style>
