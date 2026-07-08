<script setup lang="ts">
// Cross-job, cross-cluster Compare workbench (plan 077 decision 10): the tree
// browser (CompareJobTree.vue) adds any (job, experiment, app) row-set to
// one comparison canvas. Overlay/small-multiples both render through S10's
// resultsPlot.ts theme and S14's resultsCompare.ts trace builders, so both
// modes always share one axis unit (decision 11).
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import Plotly from "plotly.js-cartesian-dist-min";
import { useResultsStore } from "@/stores/results";
import { resultsKey } from "@/lib/jobKey";
import { type ChartKind, type ScaleKind } from "@/lib/resultsChart";
import { makePlotlyLayout } from "@/lib/resultsPlot";
import {
  buildChips,
  makeOverlayTraces,
  makeSmallMultipleTraces,
  seriesId,
  sharedColumns,
  sharedUnit,
  type CompareChip,
  type CompareSeries,
  type SeriesMeta,
} from "@/lib/resultsCompare";
import CompareJobTree from "@/components/results/CompareJobTree.vue";
import ResultsTabs from "@/components/results/ResultsTabs.vue";

const results = useResultsStore();
onMounted(() => {
  results.loadIndex();
});

const cachedJobs = computed(() => results.index.filter((j) => j.cached));

const selected = reactive<Map<string, SeriesMeta>>(new Map());
const selectedIds = computed(() => new Set(selected.keys()));
function toggleSeries(m: SeriesMeta) {
  const id = seriesId(m);
  if (selected.has(id)) selected.delete(id);
  else selected.set(id, m);
}

// The single place a selection's colors/labels are computed -- chips and
// chart traces both derive from this list, so they can never disagree.
const chips = computed<CompareChip[]>(() => buildChips(selected));
function removeChip(id: string) {
  const m = selected.get(id);
  if (m) toggleSeries(m);
}

const selectedSeries = computed<CompareSeries[]>(() =>
  chips.value.flatMap((c) => {
    const m = selected.get(c.id)!;
    const data = results.results[resultsKey(m.cluster, m.system, m.jobBasename)];
    const rows = data?.experiments[m.experiment]?.[m.app];
    if (!rows) return [];
    return [{ id: c.id, label: c.label, color: c.color, rows }];
  }),
);

const columns = computed(() => sharedColumns(selectedSeries.value));
const xCol = ref("");
const yCol = ref("");
watch(
  columns,
  (cols) => {
    if (!cols.includes(xCol.value)) xCol.value = cols[0] ?? "";
    if (!cols.includes(yCol.value))
      yCol.value = cols.find((c) => c !== xCol.value) ?? cols[0] ?? "";
  },
  { immediate: true },
);

const CHART_KINDS: { value: ChartKind; label: string }[] = [
  { value: "scatter", label: "Scatter" },
  { value: "line", label: "Line" },
  { value: "bar", label: "Bar" },
  { value: "violin", label: "Violin" },
];
const kind = ref<ChartKind>("scatter");
const scale = ref<ScaleKind>("linear");
type CompareMode = "overlay" | "small-multiples";
const mode = ref<CompareMode>("overlay");

const yUnit = computed(() =>
  xCol.value && yCol.value && selectedSeries.value.length
    ? sharedUnit(selectedSeries.value, yCol.value)
    : { div: 1, label: "" },
);
const xUnit = computed(() =>
  xCol.value && yCol.value && selectedSeries.value.length
    ? sharedUnit(selectedSeries.value, xCol.value)
    : { div: 1, label: "" },
);

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

const overlayEl = ref<HTMLDivElement | null>(null);
let overlayRendered = false;

function renderOverlay() {
  if (!overlayEl.value || !xCol.value || !yCol.value || !selectedSeries.value.length) {
    if (overlayRendered && overlayEl.value) {
      Plotly.purge(overlayEl.value);
      overlayRendered = false;
    }
    return;
  }
  const data = makeOverlayTraces(
    selectedSeries.value,
    xCol.value,
    yCol.value,
    kind.value,
    yUnit.value,
  );
  const layout = makePlotlyLayout(
    xCol.value,
    yCol.value,
    xUnit.value,
    yUnit.value,
    kind.value,
    scale.value,
    true,
  );
  if (overlayRendered) Plotly.react(overlayEl.value, data, layout, PLOT_CONFIG);
  else {
    Plotly.newPlot(overlayEl.value, data, layout, PLOT_CONFIG);
    overlayRendered = true;
  }
}

const smallEls = reactive<Record<string, HTMLDivElement | null>>({});
const smallRendered = new Set<string>();
function setSmallEl(id: string, el: Element | null) {
  smallEls[id] = (el as HTMLDivElement) ?? null;
}

function purgeSmallMultiples() {
  smallRendered.forEach((id) => {
    const el = smallEls[id];
    if (el) Plotly.purge(el);
  });
  smallRendered.clear();
}

function renderSmallMultiples() {
  if (!xCol.value || !yCol.value) return;
  const cards = makeSmallMultipleTraces(
    selectedSeries.value,
    xCol.value,
    yCol.value,
    kind.value,
    yUnit.value,
  );
  const layout = makePlotlyLayout(
    xCol.value,
    yCol.value,
    xUnit.value,
    yUnit.value,
    kind.value,
    scale.value,
    false,
  );
  const currentIds = new Set(cards.map((c) => c.id));
  [...smallRendered].forEach((id) => {
    if (currentIds.has(id)) return;
    const el = smallEls[id];
    if (el) Plotly.purge(el);
    smallRendered.delete(id);
  });
  cards.forEach((card) => {
    const el = smallEls[card.id];
    if (!el) return;
    if (smallRendered.has(card.id)) Plotly.react(el, card.traces, layout, PLOT_CONFIG);
    else {
      Plotly.newPlot(el, card.traces, layout, PLOT_CONFIG);
      smallRendered.add(card.id);
    }
  });
}

async function render() {
  if (mode.value === "overlay") {
    purgeSmallMultiples();
    await nextTick();
    renderOverlay();
  } else {
    if (overlayRendered && overlayEl.value) {
      Plotly.purge(overlayEl.value);
      overlayRendered = false;
    }
    await nextTick(); // small-multiple divs must exist before Plotly binds to them
    renderSmallMultiples();
  }
}

watch([selectedSeries, xCol, yCol, kind, scale, mode], render);

onUnmounted(() => {
  if (overlayRendered && overlayEl.value) Plotly.purge(overlayEl.value);
  purgeSmallMultiples();
});
</script>

<template>
  <section class="results-compare">
    <h1>Compare results</h1>
    <ResultsTabs />
    <p v-if="results.indexBusy" class="meta">Loading…</p>

    <div class="layout">
      <div class="sidebar-col">
        <div v-if="chips.length" class="chip-strip">
          <span v-for="c in chips" :key="c.id" class="chip">
            <span class="chip-swatch" :style="{ background: c.color }" />
            <span class="chip-label">{{ c.label }}</span>
            <button
              type="button"
              class="chip-remove"
              :aria-label="`Remove ${c.label} from the comparison`"
              @click="removeChip(c.id)"
            >
              &times;
            </button>
          </span>
        </div>
        <CompareJobTree :jobs="cachedJobs" :selected-ids="selectedIds" @toggle="toggleSeries" />
      </div>

      <div class="content">
        <p v-if="!selectedSeries.length" class="empty">
          Select an app from any job on the left to add it to the comparison.
        </p>
        <p v-else-if="!columns.length" class="empty">
          The selected series have no numeric column in common, pick a different combination.
        </p>
        <template v-else>
          <div class="toolbar">
            <div class="mode-picker">
              <button
                type="button"
                class="mode-btn"
                :class="{ active: mode === 'overlay' }"
                @click="mode = 'overlay'"
              >
                Overlay
              </button>
              <button
                type="button"
                class="mode-btn"
                :class="{ active: mode === 'small-multiples' }"
                @click="mode = 'small-multiples'"
              >
                Small multiples
              </button>
            </div>
            <div class="kind-picker">
              <button
                v-for="k in CHART_KINDS"
                :key="k.value"
                type="button"
                class="kind-btn"
                :class="{ active: kind === k.value }"
                @click="kind = k.value"
              >
                {{ k.label }}
              </button>
            </div>
            <label class="axis-picker">
              X
              <select v-model="xCol">
                <option v-for="c in columns" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
            <label class="axis-picker">
              Y
              <select v-model="yCol">
                <option v-for="c in columns" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
            <label class="scale-toggle" v-if="kind === 'scatter' || kind === 'line'">
              <input
                type="checkbox"
                :checked="scale === 'logarithmic'"
                @change="
                  scale = ($event.target as HTMLInputElement).checked ? 'logarithmic' : 'linear'
                "
              />
              Log scale
            </label>
          </div>

          <div v-if="mode === 'overlay'" class="overlay-wrap">
            <div ref="overlayEl" class="plot"></div>
          </div>
          <div v-else class="grid">
            <div v-for="s in selectedSeries" :key="s.id" class="card">
              <div class="card-title">{{ s.label }}</div>
              <div :ref="(el) => setSmallEl(s.id, el as Element | null)" class="plot small"></div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.results-compare {
  padding: 1.25rem 1.5rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.4rem;
  margin: 0 0 0.9rem;
}
.meta {
  color: var(--text3);
  font-size: var(--t-sm);
}
.layout {
  display: flex;
  align-items: flex-start;
  gap: 1.25rem;
}
.sidebar-col {
  flex: 0 0 17rem;
}
.chip-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
}
.chip {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  max-width: 100%;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.15rem 0.3rem 0.15rem 0.5rem;
  background: var(--bg1);
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--text2);
}
.chip-swatch {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.chip-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip-remove {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--text3);
  font-size: 0.9rem;
  line-height: 1;
  padding: 0 0.15rem;
}
.chip-remove:hover {
  color: var(--danger);
}
.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 1rem;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.mode-picker,
.kind-picker {
  display: flex;
  gap: 2px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 2px;
}
.mode-btn,
.kind-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0.25rem 0.7rem;
  border-radius: 4px;
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
}
.mode-btn.active,
.kind-btn.active {
  background: var(--bg1);
  color: var(--accent);
}
.axis-picker {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  color: var(--text2);
}
.axis-picker select {
  background: var(--bg2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.2rem 0.4rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
}
.scale-toggle {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  color: var(--text2);
}
.overlay-wrap {
  height: 480px;
}
.plot {
  width: 100%;
  height: 100%;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1rem;
}
.card {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.card-title {
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
}
.plot.small {
  height: 260px;
}
</style>
