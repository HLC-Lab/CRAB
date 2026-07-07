<script setup lang="ts">
// Overlay/small-multiples compare across a job's own experiments (single-job
// scope only — cross-cluster compare is deferred, ADR-021). Ported from
// crab_dashboard.html's CompareRenderer (selection + mode) and ChartRenderer's
// buildForCompare (overlay merge, now lib/resultsCompare.ts's
// makeOverlayChartData), restyled to this app's design tokens.
import { Chart, registerables, type ChartConfiguration } from "chart.js";
import { Violin, ViolinController } from "@sgratzl/chartjs-chart-boxplot";
import { computed, nextTick, onUnmounted, reactive, ref, watch } from "vue";
import {
  buildChartOptions,
  makeChartData,
  numericCols,
  unitForCol,
  type ChartKind,
  type ScaleKind,
} from "@/lib/resultsChart";
import { makeOverlayChartData, type CompareExperiment } from "@/lib/resultsCompare";

Chart.register(...registerables, ViolinController, Violin);

const props = defineProps<{
  experiments: CompareExperiment[];
}>();

type CompareMode = "overlay" | "small-multiples";

const CHART_KINDS: { value: ChartKind; label: string }[] = [
  { value: "scatter", label: "Scatter" },
  { value: "line", label: "Line" },
  { value: "bar", label: "Bar" },
  { value: "violin", label: "Violin" },
];

const selected = ref<Set<string>>(new Set());
const mode = ref<CompareMode>("overlay");
const kind = ref<ChartKind>("scatter");
const scale = ref<ScaleKind>("logarithmic");
const xCol = ref("");
const yCol = ref("");

function toggle(name: string) {
  const next = new Set(selected.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  selected.value = next;
}

const selectedExperiments = computed(() =>
  props.experiments.filter((e) => selected.value.has(e.name)),
);

const columns = computed(() => {
  const cols = new Set<string>();
  selectedExperiments.value.forEach((e) => numericCols(e.rows).forEach((c) => cols.add(c)));
  return [...cols];
});

watch(
  columns,
  (cols) => {
    if (!cols.includes(xCol.value)) xCol.value = cols[0] ?? "";
    if (!cols.includes(yCol.value))
      yCol.value = cols.find((c) => c !== xCol.value) ?? cols[0] ?? "";
  },
  { immediate: true },
);

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function theme() {
  return {
    text: cssVar("--text"),
    text2: cssVar("--text2"),
    bg2: cssVar("--bg2"),
    bg3: cssVar("--bg3"),
    border: cssVar("--border"),
  };
}

// -- overlay: a single chart, one dataset per selected experiment ----------
const overlayCanvas = ref<HTMLCanvasElement | null>(null);
let overlayChart: Chart | null = null;

function renderOverlay() {
  overlayChart?.destroy();
  overlayChart = null;
  if (!overlayCanvas.value || !xCol.value || !yCol.value || !selectedExperiments.value.length)
    return;

  const allYVals = selectedExperiments.value.flatMap((e) =>
    e.rows.map((r) => r[yCol.value] as number).filter((v) => v != null),
  );
  const yUnit = unitForCol(yCol.value, allYVals);
  const spec = makeOverlayChartData(
    selectedExperiments.value,
    xCol.value,
    yCol.value,
    kind.value,
    yUnit,
  );
  const xUnit = unitForCol(
    xCol.value,
    selectedExperiments.value.flatMap((e) =>
      e.rows.map((r) => r[xCol.value] as number).filter((v) => v != null),
    ),
  );
  const options = buildChartOptions(
    xCol.value,
    yCol.value,
    xUnit,
    yUnit,
    kind.value,
    scale.value,
    true,
    theme(),
  );

  const config = {
    type: kind.value,
    data: spec.labels
      ? { labels: spec.labels, datasets: spec.datasets }
      : { datasets: spec.datasets },
    options,
  } as unknown as ChartConfiguration;
  overlayChart = new Chart(overlayCanvas.value, config);
}

// -- small multiples: one small chart per selected experiment --------------
const smallCanvases = reactive<Record<string, HTMLCanvasElement | null>>({});
let smallCharts: Chart[] = [];

// Vue's function-ref callback type covers component instances too, but this
// ref only ever binds to a plain <canvas> element.
function setSmallCanvas(name: string, el: unknown) {
  smallCanvases[name] = (el as HTMLCanvasElement) ?? null;
}

function renderSmallMultiples() {
  smallCharts.forEach((c) => c.destroy());
  smallCharts = [];
  if (!xCol.value || !yCol.value) return;

  selectedExperiments.value.forEach((exp) => {
    const canvas = smallCanvases[exp.name];
    if (!canvas) return;
    const validRows = exp.rows.filter((r) => r[xCol.value] != null && r[yCol.value] != null);
    const yUnit = unitForCol(
      yCol.value,
      validRows.map((r) => r[yCol.value] as number),
    );
    const xUnit = unitForCol(
      xCol.value,
      validRows.map((r) => r[xCol.value] as number),
    );
    const spec = makeChartData(
      exp.rows,
      xCol.value,
      yCol.value,
      kind.value,
      yUnit,
      exp.color,
      exp.name,
    );
    const options = buildChartOptions(
      xCol.value,
      yCol.value,
      xUnit,
      yUnit,
      kind.value,
      scale.value,
      false,
      theme(),
    );
    const config = {
      type: kind.value,
      data: spec.labels
        ? { labels: spec.labels, datasets: spec.datasets }
        : { datasets: spec.datasets },
      options,
    } as unknown as ChartConfiguration;
    smallCharts.push(new Chart(canvas, config));
  });
}

async function render() {
  if (mode.value === "overlay") {
    smallCharts.forEach((c) => c.destroy());
    smallCharts = [];
    await nextTick();
    renderOverlay();
  } else {
    overlayChart?.destroy();
    overlayChart = null;
    await nextTick(); // small-multiple canvases must exist before Chart.js binds to them
    renderSmallMultiples();
  }
}

onUnmounted(() => {
  overlayChart?.destroy();
  smallCharts.forEach((c) => c.destroy());
});

watch([selectedExperiments, xCol, yCol, kind, scale, mode], render, { immediate: true });
</script>

<template>
  <div class="compare-view">
    <div class="selector">
      <label v-for="exp in experiments" :key="exp.name" class="exp-opt">
        <input type="checkbox" :checked="selected.has(exp.name)" @change="toggle(exp.name)" />
        <span class="dot" :style="{ background: exp.color }"></span>
        {{ exp.name }}
      </label>
    </div>

    <p v-if="!selectedExperiments.length" class="empty">
      Select two or more experiments above to compare them.
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
      </div>

      <div v-if="mode === 'overlay'" class="canvas-wrap">
        <canvas ref="overlayCanvas"></canvas>
      </div>
      <div v-else class="small-grid">
        <div v-for="exp in selectedExperiments" :key="exp.name" class="small-card">
          <div class="small-title">
            <span class="dot" :style="{ background: exp.color }"></span>{{ exp.name }}
          </div>
          <div class="small-canvas-wrap">
            <canvas :ref="(el) => setSmallCanvas(exp.name, el)"></canvas>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.compare-view {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.selector {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}
.exp-opt {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  color: var(--text2);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
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
.canvas-wrap {
  position: relative;
  height: 400px;
}
.small-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0.75rem;
}
.small-card {
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.5rem;
  background: var(--bg2);
}
.small-title {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  color: var(--text2);
  margin-bottom: 0.4rem;
}
.small-canvas-wrap {
  position: relative;
  height: 220px;
}
</style>
