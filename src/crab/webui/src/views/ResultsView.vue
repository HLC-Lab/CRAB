<script setup lang="ts">
// Top-level Results picker (plan 065): lists jobs that already have a fetched
// results cache, linking into each one's detail-view Results tab. There is no
// backend "list jobs with cached results" endpoint (per-job cache presence is
// cheap to check locally), so this checks each known job the same way the
// per-job Results tab does — reusing loadResults, not a new store method.
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useJobsStore } from "@/stores/jobs";
import { useResultsStore } from "@/stores/results";

const jobs = useJobsStore();
const results = useResultsStore();

onMounted(async () => {
  if (!jobs.items.length) await jobs.refresh();
  await Promise.all(jobs.items.map((j) => results.loadResults(j.id)));
});

const cachedJobs = computed(() => jobs.items.filter((j) => results.results[j.id]));
</script>

<template>
  <section class="results-view">
    <h1>Results</h1>
    <p class="blurb">
      Fetch a job's result data from its cluster to explore scatter/line/bar/violin charts, a
      sortable table, and a cross-experiment compare — from that job's detail view.
    </p>

    <p v-if="jobs.loading" class="meta">Loading jobs…</p>
    <p v-else-if="!cachedJobs.length" class="empty">
      No jobs have fetched results yet. Open a job's detail view and use its Results tab to fetch
      one.
    </p>

    <ul v-else class="list">
      <li v-for="j in cachedJobs" :key="j.id" class="item">
        <RouterLink :to="`/jobs/${j.id}`">{{ j.config_name }}</RouterLink>
        <span class="sub">{{ j.cluster }} / {{ j.system }} · job {{ j.job_id }}</span>
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
  margin: 0 0 0.4rem;
}
.blurb {
  color: var(--text2);
  font-size: var(--t-sm);
  margin-bottom: 1rem;
}
.meta,
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
}
.list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.item {
  display: flex;
  flex-direction: column;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg1);
}
.item a {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}
.item a:hover {
  text-decoration: underline;
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
}
</style>
