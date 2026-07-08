<script setup lang="ts">
// The Results body for one job (plan 065, terminology/layout fixed in plan
// 077 S12): fetch-on-demand, then a two-column layout -- a left sidebar
// listing Experiments (the config's experiment key, expandable to the Apps
// run within it) and a right-hand Chart/Table area for whichever App is
// selected. `data`'s absence covers both "never fetched" and "cache just
// cleared" uniformly -- no need to distinguish them in the UI, both just show
// the fetch prompt. Compare is temporarily absent (plan 077 S9-S15): the
// generalized cross-job workbench replaces it.
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useResultsStore } from "@/stores/results";
import { assignColors, formatBytes } from "@/lib/resultsChart";
import { resultsKey } from "@/lib/jobKey";
import { runFailureNote } from "@/lib/jobStatus";
import ResultsChart from "@/components/results/ResultsChart.vue";
import ResultsTable from "@/components/results/ResultsTable.vue";

const props = defineProps<{ cluster: string; system: string; jobBasename: string }>();
const results = useResultsStore();
const key = computed(() => resultsKey(props.cluster, props.system, props.jobBasename));

onMounted(() => {
  results.loadResults(props.cluster, props.system, props.jobBasename);
  results.loadExperiments(props.cluster, props.system, props.jobBasename);
  results.refreshCacheSize();
});

const data = computed(() => results.results[key.value]);
const experimentNames = computed(() => (data.value ? Object.keys(data.value.experiments) : []));

// Per-experiment run-failure notes (plan 081), keyed by experiment name --
// loaded independently of `data`, so a note can show even before/without the
// CSV tree being fetched.
const runFailureNotes = computed(() => {
  const notes: Record<string, string> = {};
  for (const exp of results.experiments[key.value] ?? []) {
    const note = runFailureNote(exp);
    if (note) notes[exp.experiment_name] = note;
  }
  return notes;
});
const activeExperiment = ref("");
const expanded = reactive<Record<string, boolean>>({});
watch(
  experimentNames,
  (names) => {
    if (!names.includes(activeExperiment.value)) activeExperiment.value = names[0] ?? "";
    if (activeExperiment.value) expanded[activeExperiment.value] = true;
  },
  { immediate: true },
);

function appNamesFor(experiment: string): string[] {
  return data.value ? Object.keys(data.value.experiments[experiment] ?? {}) : [];
}

const appNames = computed(() => appNamesFor(activeExperiment.value));
const activeApp = ref("");
watch(
  appNames,
  (names) => {
    if (!names.includes(activeApp.value)) activeApp.value = names[0] ?? "";
  },
  { immediate: true },
);

const colors = computed(() => assignColors(appNames.value));
const activeRows = computed(() =>
  activeExperiment.value && activeApp.value && data.value
    ? data.value.experiments[activeExperiment.value][activeApp.value]
    : [],
);

function selectApp(experiment: string, app: string) {
  activeExperiment.value = experiment;
  activeApp.value = app;
  expanded[experiment] = true;
}

function toggleExpand(experiment: string) {
  expanded[experiment] = !expanded[experiment];
}

type SubView = "chart" | "table";
const SUB_VIEWS: { value: SubView; label: string }[] = [
  { value: "chart", label: "Chart" },
  { value: "table", label: "Table" },
];
const subView = ref<SubView>("chart");

async function fetchNow() {
  await results.fetchResults(props.cluster, props.system, props.jobBasename);
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

    <p v-if="results.loadBusy[key]" class="meta">Checking for cached results…</p>
    <p v-else-if="results.loadError[key]" class="banner err">
      {{ results.loadError[key] }}
    </p>

    <div v-else-if="!data" class="fetch-prompt">
      <p class="meta">No results fetched yet for this job.</p>
      <button class="btn primary" :disabled="results.fetchBusy[key]" @click="fetchNow">
        {{ results.fetchBusy[key] ? "Fetching…" : "Fetch results" }}
      </button>
      <p v-if="results.fetchError[key]" class="banner err small">
        {{ results.fetchError[key] }}
      </p>
    </div>

    <div v-else class="layout">
      <nav class="sidebar">
        <div v-for="exp in experimentNames" :key="exp" class="exp-group">
          <button
            type="button"
            class="exp-header"
            :class="{ active: exp === activeExperiment }"
            @click="toggleExpand(exp)"
          >
            <span class="chevron" :class="{ open: expanded[exp] }">&rsaquo;</span>
            {{ exp }}
            <span v-if="runFailureNotes[exp]" class="run-failure-note">
              {{ runFailureNotes[exp] }}
            </span>
          </button>
          <ul v-if="expanded[exp]" class="app-list">
            <li v-for="app in appNamesFor(exp)" :key="app">
              <button
                type="button"
                class="app-item"
                :class="{ active: exp === activeExperiment && app === activeApp }"
                @click="selectApp(exp, app)"
              >
                {{ app }}
              </button>
            </li>
          </ul>
        </div>
      </nav>

      <div class="content">
        <div class="toolbar">
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
          <button class="btn" :disabled="results.fetchBusy[key]" @click="fetchNow">
            {{ results.fetchBusy[key] ? "Refetching…" : "Refetch" }}
          </button>
        </div>
        <p v-if="results.fetchError[key]" class="banner err small">
          {{ results.fetchError[key] }}
        </p>

        <ResultsChart
          v-if="subView === 'chart'"
          :rows="activeRows"
          :label="activeApp"
          :color="colors[activeApp] ?? '#4e79a7'"
        />
        <ResultsTable v-else :rows="activeRows" />
      </div>
    </div>
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
.layout {
  display: flex;
  align-items: flex-start;
  gap: 1.25rem;
  min-height: 480px;
}
.sidebar {
  flex: 0 0 15rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.4rem;
  background: var(--bg1);
}
.exp-group {
  display: flex;
  flex-direction: column;
}
.exp-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border-radius: var(--r);
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
  word-break: break-all;
}
.exp-header:hover {
  background: var(--bg2);
}
.exp-header.active {
  color: var(--accent);
}
.run-failure-note {
  color: var(--warn);
  font-size: 0.7rem;
  white-space: nowrap;
}
.chevron {
  display: inline-block;
  transition: transform 0.15s;
  flex-shrink: 0;
}
.chevron.open {
  transform: rotate(90deg);
}
.app-list {
  list-style: none;
  margin: 0;
  padding: 0 0 0 1.3rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.app-item {
  display: block;
  width: 100%;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  padding: 0.3rem 0.5rem;
  border-radius: var(--r);
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text3);
}
.app-item:hover {
  background: var(--bg2);
  color: var(--text);
}
.app-item.active {
  background: var(--bg2);
  color: var(--accent);
}
.content {
  flex: 1;
  min-width: 0;
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
