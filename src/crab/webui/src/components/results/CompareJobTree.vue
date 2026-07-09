<script setup lang="ts">
// Sidebar tree for the Compare workbench (plan 077 S15): Job -> Experiment ->
// App checkboxes. A job's full ResultsData only loads once its node is
// expanded (lazy -- avoids pulling the whole cache into memory at once).
import { reactive } from "vue";
import { useResultsStore } from "@/stores/results";
import { resultsKey } from "@/lib/jobKey";
import { seriesId, type SeriesMeta } from "@/lib/resultsCompare";
import type { ResultsJobEntry } from "@/api/types";

const props = defineProps<{ jobs: ResultsJobEntry[]; selectedIds: Set<string> }>();
const emit = defineEmits<{ toggle: [meta: SeriesMeta] }>();

const results = useResultsStore();

function jobKey(j: ResultsJobEntry): string {
  return resultsKey(j.cluster, j.system, j.job_basename);
}

const expanded = reactive<Record<string, boolean>>({});
function toggleExpand(j: ResultsJobEntry) {
  const key = jobKey(j);
  expanded[key] = !expanded[key];
  if (expanded[key] && !results.results[key] && !results.loadBusy[key]) {
    results.loadResults(j.cluster, j.system, j.job_basename);
  }
}

function meta(j: ResultsJobEntry, experiment: string, app: string): SeriesMeta {
  return { cluster: j.cluster, system: j.system, jobBasename: j.job_basename, experiment, app };
}
</script>

<template>
  <nav class="sidebar">
    <p v-if="!props.jobs.length" class="meta">No cached jobs to compare yet.</p>
    <div v-for="j in props.jobs" :key="jobKey(j)" class="job-group">
      <button type="button" class="job-header" :title="j.job_basename" @click="toggleExpand(j)">
        <span class="chevron" :class="{ open: expanded[jobKey(j)] }">&rsaquo;</span>
        <span
          class="dot"
          :class="j.connected ? 'on' : 'off'"
          :title="j.connected ? 'cluster connected' : 'cluster not connected'"
        />
        <span class="job-name">{{ j.job_basename }}</span>
        <span class="job-sub">{{ j.cluster }}/{{ j.system }}</span>
      </button>
      <div v-if="expanded[jobKey(j)]" class="job-body">
        <p v-if="results.loadBusy[jobKey(j)]" class="meta small">Loading…</p>
        <p v-else-if="results.loadError[jobKey(j)]" class="meta small err">
          {{ results.loadError[jobKey(j)] }}
        </p>
        <div
          v-for="(apps, exp) in results.results[jobKey(j)]?.experiments"
          :key="exp"
          class="exp-group"
        >
          <div class="exp-label" :title="String(exp)">{{ exp }}</div>
          <label v-for="app in Object.keys(apps)" :key="app" class="app-checkbox" :title="app">
            <input
              type="checkbox"
              :checked="props.selectedIds.has(seriesId(meta(j, String(exp), app)))"
              @change="emit('toggle', meta(j, String(exp), app))"
            />
            <span class="app-name">{{ app }}</span>
          </label>
        </div>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.sidebar {
  flex: 0 0 17rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.4rem;
  background: var(--bg1);
  max-height: 75vh;
  overflow-y: auto;
}
.job-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border-radius: var(--r);
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
}
.job-header:hover {
  background: var(--bg2);
}
.job-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.job-sub {
  flex-shrink: 0;
  margin-left: auto;
  color: var(--text3);
  font-size: 0.75rem;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.on {
  background: var(--ok);
}
.dot.off {
  background: var(--text3);
}
.chevron {
  display: inline-block;
  transition: transform 0.15s;
  flex-shrink: 0;
}
.chevron.open {
  transform: rotate(90deg);
}
.job-body {
  padding: 0 0 0.4rem 1.2rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.exp-group {
  display: flex;
  flex-direction: column;
}
.exp-label {
  color: var(--text3);
  font-size: 0.75rem;
  margin-top: 0.2rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-checkbox {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.15rem 0.3rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
  cursor: pointer;
}
.app-checkbox input[type="checkbox"] {
  appearance: none;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  margin: 0;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--bg1);
  cursor: pointer;
  position: relative;
}
.app-checkbox input[type="checkbox"]:checked {
  background: var(--accent);
  border-color: var(--accent);
}
.app-checkbox input[type="checkbox"]:checked::after {
  content: "";
  position: absolute;
  left: 4px;
  top: 1px;
  width: 4px;
  height: 8px;
  border: solid var(--bg1);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
.app-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  color: var(--text3);
  font-size: var(--t-sm);
}
.meta.small {
  font-size: 0.75rem;
}
.meta.err {
  color: var(--danger);
}
</style>
