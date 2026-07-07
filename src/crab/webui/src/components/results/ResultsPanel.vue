<script setup lang="ts">
// The Results tab body for one job (plan 065): fetch-on-demand, then a
// lab/experiment picker over Chart/Table/Compare. `data`'s absence covers
// both "never fetched" and "cache just cleared" uniformly — no need to
// distinguish them in the UI, both just show the fetch prompt.
import { computed, onMounted, ref, watch } from "vue";
import { useResultsStore } from "@/stores/results";
import { assignColors, formatBytes } from "@/lib/resultsChart";
import ResultsChart from "@/components/results/ResultsChart.vue";
import ResultsTable from "@/components/results/ResultsTable.vue";
import CompareView from "@/components/results/CompareView.vue";

const props = defineProps<{ recordId: string }>();
const results = useResultsStore();

onMounted(() => {
  results.loadResults(props.recordId);
  results.refreshCacheSize();
});

const data = computed(() => results.results[props.recordId]);
const labs = computed(() => (data.value ? Object.keys(data.value.labs) : []));
const activeLab = ref("");
watch(
  labs,
  (ls) => {
    if (!ls.includes(activeLab.value)) activeLab.value = ls[0] ?? "";
  },
  { immediate: true },
);

const experimentNames = computed(() =>
  activeLab.value && data.value ? Object.keys(data.value.labs[activeLab.value]) : [],
);
const activeExperiment = ref("");
watch(
  experimentNames,
  (names) => {
    if (!names.includes(activeExperiment.value)) activeExperiment.value = names[0] ?? "";
  },
  { immediate: true },
);

const colors = computed(() => assignColors(experimentNames.value));
const activeRows = computed(() =>
  activeLab.value && activeExperiment.value && data.value
    ? data.value.labs[activeLab.value][activeExperiment.value]
    : [],
);
const compareExperiments = computed(() =>
  !activeLab.value || !data.value
    ? []
    : experimentNames.value.map((name) => ({
        name,
        color: colors.value[name],
        rows: data.value!.labs[activeLab.value][name],
      })),
);

type SubView = "chart" | "table" | "compare";
const SUB_VIEWS: { value: SubView; label: string }[] = [
  { value: "chart", label: "Chart" },
  { value: "table", label: "Table" },
  { value: "compare", label: "Compare" },
];
const subView = ref<SubView>("chart");

async function fetchNow() {
  await results.fetchResults(props.recordId);
}

async function clearCache() {
  await results.clearCache();
}
</script>

<template>
  <div class="results-panel">
    <div class="cache-bar">
      <span class="meta">
        Results cache: {{ results.cacheSize != null ? formatBytes(results.cacheSize) : "…" }}
      </span>
      <button class="btn" :disabled="results.clearBusy" @click="clearCache">Clear cache</button>
    </div>
    <p v-if="results.clearError" class="banner err small">{{ results.clearError }}</p>

    <p v-if="results.loadBusy[recordId]" class="meta">Checking for cached results…</p>
    <p v-else-if="results.loadError[recordId]" class="banner err">
      {{ results.loadError[recordId] }}
    </p>

    <div v-else-if="!data" class="fetch-prompt">
      <p class="meta">No results fetched yet for this job.</p>
      <button class="btn primary" :disabled="results.fetchBusy[recordId]" @click="fetchNow">
        {{ results.fetchBusy[recordId] ? "Fetching…" : "Fetch results" }}
      </button>
      <p v-if="results.fetchError[recordId]" class="banner err small">
        {{ results.fetchError[recordId] }}
      </p>
    </div>

    <template v-else>
      <div class="toolbar">
        <label v-if="labs.length > 1" class="picker">
          Lab
          <select v-model="activeLab">
            <option v-for="l in labs" :key="l" :value="l">{{ l }}</option>
          </select>
        </label>
        <label class="picker">
          Experiment
          <select v-model="activeExperiment">
            <option v-for="n in experimentNames" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
        <div class="subview-picker">
          <button
            v-for="sv in SUB_VIEWS"
            :key="sv.value"
            type="button"
            class="sv-btn"
            :class="{ active: subView === sv.value }"
            @click="subView = sv.value"
          >
            {{ sv.label }}
          </button>
        </div>
        <button class="btn" :disabled="results.fetchBusy[recordId]" @click="fetchNow">
          {{ results.fetchBusy[recordId] ? "Refetching…" : "Refetch" }}
        </button>
      </div>
      <p v-if="results.fetchError[recordId]" class="banner err small">
        {{ results.fetchError[recordId] }}
      </p>

      <ResultsChart
        v-if="subView === 'chart'"
        :rows="activeRows"
        :label="activeExperiment"
        :color="colors[activeExperiment] ?? '#4e79a7'"
      />
      <ResultsTable v-else-if="subView === 'table'" :rows="activeRows" />
      <CompareView v-else :experiments="compareExperiments" />
    </template>
  </div>
</template>

<style scoped>
.results-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.cache-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.meta {
  color: var(--text3);
  font-size: var(--t-sm);
}
.fetch-prompt {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 1rem;
  border: 1px dashed var(--border2);
  border-radius: var(--r2);
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.picker {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  color: var(--text2);
}
.picker select {
  background: var(--bg2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.2rem 0.4rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
}
.subview-picker {
  display: flex;
  gap: 2px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 2px;
}
.sv-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0.25rem 0.7rem;
  border-radius: 4px;
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
}
.sv-btn.active {
  background: var(--bg1);
  color: var(--accent);
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
}
.btn:hover {
  border-color: var(--accent);
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.banner {
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
  white-space: pre-wrap;
}
.banner.small {
  font-size: 0.8rem;
}
</style>
