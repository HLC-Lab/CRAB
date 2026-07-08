<script setup lang="ts">
// Top-level Results picker (plan 077 S13), consuming S6's cross-cluster
// index -- shows every job crab history reports (dashboard-submitted or
// CLI-only alike) with real metadata and a staleness badge. Card layout and
// filters mirror JobsView.vue's established pattern (plan 080), for
// consistency across the app rather than a bespoke Results-only style.
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { useResultsStore } from "@/stores/results";
import { formatBytes } from "@/lib/resultsChart";
import { describeStaleness, filterEntries, sortEntries } from "@/lib/resultsIndex";
import ResultsTabs from "@/components/results/ResultsTabs.vue";

const results = useResultsStore();

onMounted(() => {
  results.loadIndex();
});

const sorted = computed(() => sortEntries(results.index));

const search = ref("");
const clusterFilter = ref<Set<string>>(new Set());
const stalenessFilter = ref<Set<string>>(new Set());

const entries = computed(() =>
  filterEntries(sorted.value, {
    search: search.value,
    clusters: clusterFilter.value,
    staleness: stalenessFilter.value,
  }),
);

// Chip options are drawn from the unfiltered list so a chip never disappears
// just because the current filter narrowed things to zero (same convention
// as JobsView's cluster/status chips).
const availableClusters = computed(() => [...new Set(sorted.value.map((e) => e.cluster))].sort());
const STALENESS_ORDER = ["Not fetched yet", "Possibly stale", "Up to date"];
const availableStaleness = computed(() =>
  STALENESS_ORDER.filter((label) => sorted.value.some((e) => describeStaleness(e).label === label)),
);

function toggleCluster(name: string) {
  const next = new Set(clusterFilter.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  clusterFilter.value = next;
}
function toggleStaleness(label: string) {
  const next = new Set(stalenessFilter.value);
  if (next.has(label)) next.delete(label);
  else next.add(label);
  stalenessFilter.value = next;
}
</script>

<template>
  <section class="results-view">
    <h1>Results</h1>

    <ResultsTabs />

    <div v-if="sorted.length" class="filters">
      <input class="search" type="search" placeholder="Search job..." v-model="search" />
      <div class="chip-row">
        <span class="chip-label">Cluster:</span>
        <div class="chips">
          <button
            v-for="c in availableClusters"
            :key="c"
            class="chip"
            :class="{ on: clusterFilter.has(c) }"
            @click="toggleCluster(c)"
          >
            {{ c }}
          </button>
        </div>
      </div>
      <div class="chip-row">
        <span class="chip-label">Status:</span>
        <div class="chips">
          <button
            v-for="label in availableStaleness"
            :key="label"
            class="chip"
            :class="{ on: stalenessFilter.has(label) }"
            @click="toggleStaleness(label)"
          >
            {{ label }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="results.indexBusy" class="meta">Loading…</p>
    <p v-else-if="results.indexError" class="banner err">{{ results.indexError }}</p>
    <p v-else-if="!sorted.length" class="empty">No jobs found on any connected cluster.</p>
    <p v-else-if="!entries.length" class="empty">No jobs match the current filters.</p>

    <ul v-else class="list">
      <li v-for="e in entries" :key="`${e.cluster}/${e.system}/${e.job_basename}`" class="card">
        <RouterLink :to="`/results/${e.cluster}/${e.system}/${e.job_basename}`" class="card-link">
          <div class="top">
            <span
              class="dot"
              :class="e.connected ? 'on' : 'off'"
              :title="
                e.connected
                  ? 'cluster connected'
                  : 'cluster not connected — showing last known state'
              "
            />
            <span class="identity">{{ e.cluster }} / {{ e.system }}</span>
            <span v-if="e.submitted_at" class="meta submitted-at">
              submitted {{ new Date(e.submitted_at).toLocaleString() }}
            </span>
          </div>
          <div class="title-row">
            <span class="use-case">{{ e.job_basename }}</span>
            <span class="state" :class="describeStaleness(e).tone">
              {{ describeStaleness(e).label }}
            </span>
          </div>
          <p class="meta">{{ e.cached ? formatBytes(e.cached_bytes ?? 0) : "not fetched yet" }}</p>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.results-view {
  padding: 1.25rem 1.5rem;
  max-width: 50rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.4rem;
  margin: 0 0 0.9rem;
}
.meta,
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
}
.banner {
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
  white-space: pre-wrap;
}
.filters {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.search {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.6rem;
  font-family: var(--sans);
  font-size: var(--t-sm);
  min-width: 14rem;
}
.chip-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.chip-label {
  color: var(--text3);
  font-size: var(--t-sm);
  min-width: 4rem;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.chip {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text3);
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
  cursor: pointer;
}
.chip:hover {
  border-color: var(--accent);
}
.chip.on {
  border-color: var(--accent);
  color: var(--accent);
}
.list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  margin-bottom: 0.75rem;
}
.card-link {
  display: block;
  padding: 1rem;
  color: inherit;
  text-decoration: none;
  cursor: pointer;
}
.card:hover {
  border-color: var(--accent);
}
.card:hover .use-case {
  color: var(--accent);
}
.top {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}
.identity {
  color: var(--text3);
  font-size: var(--t-sm);
  font-family: var(--mono);
}
.meta.submitted-at {
  margin-top: 0;
  margin-left: auto;
  white-space: nowrap;
}
.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 0.3rem;
}
.use-case {
  color: var(--text);
  font-weight: 600;
  font-size: var(--t-lg);
  font-family: var(--mono);
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.on {
  background: var(--ok);
}
.dot.off {
  background: var(--text3);
}
.state {
  font-family: var(--mono);
  font-size: var(--t-sm);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  white-space: nowrap;
}
.state.ok {
  color: var(--ok);
  border-color: var(--ok);
}
.state.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.state.muted {
  color: var(--text3);
}
.meta {
  margin-top: 0.4rem;
  color: var(--text3);
  font-size: var(--t-sm);
}
</style>
