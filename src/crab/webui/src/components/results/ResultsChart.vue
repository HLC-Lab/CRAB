<script setup lang="ts">
// Scatter/line/bar/violin chart over one experiment's rows, with axis pickers
// and a linear/log scale toggle. Ported from crab_dashboard.html's
// ChartRenderer module (trace/layout logic lives in lib/resultsPlot.ts, pure
// and unit-tested there; this component only owns the Plotly lifecycle and
// the picker UI). Print/paper themed regardless of app dark/light mode.
import Plotly from "@/lib/plotlyBundle";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  numericCols,
  unitForCol,
  type ChartKind,
  type ResultRow,
  type ScaleKind,
} from "@/lib/resultsChart";
import {
  defaultAxisPair,
  exportChartImage,
  makePlotlyLayout,
  makePlotlyTraces,
} from "@/lib/resultsPlot";

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
const scale = ref<ScaleKind>("linear");
const xCol = ref("");
const yCol = ref("");

// Keep the axis pickers valid as the active experiment (and therefore its
// column set) changes; default via decision 5's sweep/metric heuristic.
watch(
  columns,
  (cols) => {
    if (cols.includes(xCol.value) && cols.includes(yCol.value)) return;
    const pair = defaultAxisPair(cols);
    xCol.value = pair.x;
    yCol.value = pair.y;
  },
  { immediate: true },
);

const plotEl = ref<HTMLDivElement | null>(null);
let rendered = false;

function render() {
  if (!plotEl.value || !xCol.value || !yCol.value || !props.rows.length) {
    if (rendered && plotEl.value) {
      Plotly.purge(plotEl.value);
      rendered = false;
    }
    return;
  }

  const validRows = props.rows.filter((r) => r[xCol.value] != null && r[yCol.value] != null);
  const yUnit = unitForCol(
    yCol.value,
    validRows.map((r) => r[yCol.value] as number),
  );
  const xUnit = unitForCol(
    xCol.value,
    validRows.map((r) => r[xCol.value] as number),
  );
  const data = makePlotlyTraces(
    props.rows,
    xCol.value,
    yCol.value,
    kind.value,
    yUnit,
    props.color,
    props.label,
  );
  const layout = makePlotlyLayout(
    xCol.value,
    yCol.value,
    xUnit,
    yUnit,
    kind.value,
    scale.value,
    false,
  );
  const config = { displayModeBar: false, responsive: true };

  if (rendered) {
    Plotly.react(plotEl.value, data, layout, config);
  } else {
    Plotly.newPlot(plotEl.value, data, layout, config);
    rendered = true;
  }
}

async function exportImage() {
  if (!plotEl.value || !rendered) return;
  await exportChartImage(plotEl.value, "png", props.label || "chart");
}

onMounted(render);
onUnmounted(() => {
  if (plotEl.value && rendered) Plotly.purge(plotEl.value);
  rendered = false;
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
          :checked="scale === 'logarithmic'"
          @change="scale = ($event.target as HTMLInputElement).checked ? 'logarithmic' : 'linear'"
        />
        Log scale
      </label>

      <button v-if="columns.length" type="button" class="export-btn" @click="exportImage">
        Export image
      </button>
    </div>

    <div class="canvas-wrap">
      <div v-if="columns.length" ref="plotEl" class="plot"></div>
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
.export-btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.25rem 0.7rem;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  margin-left: auto;
}
.export-btn:hover {
  border-color: var(--accent);
}
.canvas-wrap {
  position: relative;
  height: 360px;
}
.plot {
  width: 100%;
  height: 100%;
}
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 1rem;
}
</style>
