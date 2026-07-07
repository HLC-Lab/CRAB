<script setup lang="ts">
// Scatter/line/bar/violin chart over one experiment's rows, with axis pickers
// and a linear/log scale toggle. Ported from crab_dashboard.html's
// ChartRenderer module (dataset/options logic lives in lib/resultsChart.ts,
// pure and unit-tested there; this component only owns the canvas lifecycle
// and the picker UI), restyled to this app's design tokens.
import { Chart, type ChartConfiguration } from "chart.js";
import { registerables } from "chart.js";
import { Violin, ViolinController } from "@sgratzl/chartjs-chart-boxplot";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  buildChartOptions,
  makeChartData,
  numericCols,
  unitForCol,
  type ChartKind,
  type ResultRow,
  type ScaleKind,
} from "@/lib/resultsChart";

Chart.register(...registerables, ViolinController, Violin);

const props = defineProps<{
  rows: ResultRow[];
  label: string;
  color: string;
}>();

const CHART_KINDS: { value: ChartKind; label: string }[] = [
  { value: "scatter", label: "Scatter" },
  { value: "line", label: "Line" },
  { value: "bar", label: "Bar" },
  { value: "violin", label: "Violin" },
];

const columns = computed(() => numericCols(props.rows));
const kind = ref<ChartKind>("scatter");
const scale = ref<ScaleKind>("logarithmic");
const xCol = ref("");
const yCol = ref("");

// Keep the axis pickers valid as the active experiment (and therefore its
// column set) changes; default to the first two numeric columns.
watch(
  columns,
  (cols) => {
    if (!cols.includes(xCol.value)) xCol.value = cols[0] ?? "";
    if (!cols.includes(yCol.value))
      yCol.value = cols.find((c) => c !== xCol.value) ?? cols[0] ?? "";
  },
  { immediate: true },
);

const canvasEl = ref<HTMLCanvasElement | null>(null);
let chart: Chart | null = null;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function render() {
  chart?.destroy();
  chart = null;
  if (!canvasEl.value || !xCol.value || !yCol.value || !props.rows.length) return;

  const validRows = props.rows.filter((r) => r[xCol.value] != null && r[yCol.value] != null);
  const yUnit = unitForCol(
    yCol.value,
    validRows.map((r) => r[yCol.value] as number),
  );
  const xUnit = unitForCol(
    xCol.value,
    validRows.map((r) => r[xCol.value] as number),
  );
  const spec = makeChartData(
    props.rows,
    xCol.value,
    yCol.value,
    kind.value,
    yUnit,
    props.color,
    props.label,
  );
  const theme = {
    text: cssVar("--text"),
    text2: cssVar("--text2"),
    bg2: cssVar("--bg2"),
    bg3: cssVar("--bg3"),
    border: cssVar("--border"),
  };
  const options = buildChartOptions(
    xCol.value,
    yCol.value,
    xUnit,
    yUnit,
    kind.value,
    scale.value,
    false,
    theme,
  );

  // Chart.js's config type is generic per chart kind; this component builds
  // the same loose config shape the legacy dashboard did (chart type picked
  // at runtime), so the union is collapsed here rather than fought.
  const config = {
    type: kind.value,
    data: spec.labels
      ? { labels: spec.labels, datasets: spec.datasets }
      : { datasets: spec.datasets },
    options,
  } as unknown as ChartConfiguration;

  chart = new Chart(canvasEl.value, config);
}

onMounted(render);
onUnmounted(() => {
  chart?.destroy();
  chart = null;
});

watch([() => props.rows, xCol, yCol, kind, scale, () => props.color], render);
</script>

<template>
  <div class="results-chart">
    <div class="toolbar">
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
          :checked="scale === 'linear'"
          @change="scale = ($event.target as HTMLInputElement).checked ? 'linear' : 'logarithmic'"
        />
        Linear scale
      </label>
    </div>

    <div class="canvas-wrap">
      <canvas v-if="columns.length" ref="canvasEl"></canvas>
      <p v-else class="empty">No numeric columns to chart for this experiment.</p>
    </div>
  </div>
</template>

<style scoped>
.results-chart {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.kind-picker {
  display: flex;
  gap: 2px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 2px;
}
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
.canvas-wrap {
  position: relative;
  height: 360px;
}
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 1rem;
}
</style>
