<script setup lang="ts">
// Top-level Results picker (plan 077 S13), consuming S6's cross-cluster
// index -- shows every job crab history reports (dashboard-submitted or
// CLI-only alike) with real metadata and a staleness badge, no
// instructional blurb needed once that's on screen.
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useResultsStore } from "@/stores/results";
import { formatBytes } from "@/lib/resultsChart";
import { describeStaleness, sortEntries } from "@/lib/resultsIndex";

const results = useResultsStore();

onMounted(() => {
  results.loadIndex();
});

const entries = computed(() => sortEntries(results.index));
</script>

<template>
  <section class="results-view">
    <h1>Results</h1>

    <p v-if="results.indexBusy" class="meta">Loading…</p>
    <p v-else-if="results.indexError" class="banner err">{{ results.indexError }}</p>
    <p v-else-if="!entries.length" class="empty">No jobs found on any connected cluster.</p>

    <ul v-else class="list">
      <li v-for="e in entries" :key="`${e.cluster}/${e.system}/${e.job_basename}`" class="item">
        <RouterLink :to="`/results/${e.cluster}/${e.system}/${e.job_basename}`" class="name">
          {{ e.job_basename }}
        </RouterLink>
        <span class="sub">
          {{ e.cluster }} / {{ e.system }}
          <template v-if="e.submitted_at">
            · submitted {{ new Date(e.submitted_at).toLocaleString() }}
          </template>
          · {{ e.cached ? formatBytes(e.cached_bytes ?? 0) : "not fetched yet" }}
        </span>
        <span class="badge" :class="describeStaleness(e).tone">
          {{ describeStaleness(e).label }}
        </span>
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
.list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0;
  margin: 0;
}
.item {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.6rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg1);
}
.name {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
  font-family: var(--mono);
}
.name:hover {
  text-decoration: underline;
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
  flex: 1;
}
.badge {
  font-size: var(--t-sm);
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  white-space: nowrap;
}
.badge.muted {
  color: var(--text3);
  background: var(--bg3);
}
.badge.warn {
  color: var(--warn);
  background: rgba(237, 137, 54, 0.12);
}
.badge.ok {
  color: var(--ok);
  background: rgba(72, 187, 120, 0.12);
}
</style>
