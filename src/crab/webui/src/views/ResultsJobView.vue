<script setup lang="ts">
// Results as its own top-level destination (plan 077, decision 1): keyed on
// (cluster, system, jobBasename) instead of a registry record id, so a
// CLI-only job (no JobsStore record) works identically to a
// dashboard-submitted one. A matching JobsStore record is an optional join,
// purely for the "View job details" action -- never required to render.
// Nav coherence (owner feedback, 2026-07-08): the back arrow always returns
// to the Results picker, matching where every entry point into this page
// comes from; a separate, explicit action goes to the job's own detail page.
import { computed, onMounted } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useJobsStore } from "@/stores/jobs";
import { jobBasenameFromDataDir } from "@/lib/jobKey";
import ResultsPanel from "@/components/results/ResultsPanel.vue";

const route = useRoute();
const cluster = computed(() => String(route.params.cluster));
const system = computed(() => String(route.params.system));
const jobBasename = computed(() => String(route.params.jobBasename));

const jobs = useJobsStore();
onMounted(() => {
  if (!jobs.items.length) jobs.refresh();
});

const record = computed(() =>
  jobs.items.find(
    (j) =>
      j.cluster === cluster.value &&
      j.system === system.value &&
      jobBasenameFromDataDir(j.data_dir) === jobBasename.value,
  ),
);
</script>

<template>
  <section class="results-job">
    <RouterLink to="/results" class="back">&larr; Results</RouterLink>

    <div class="head">
      <div>
        <h1>{{ jobBasename }}</h1>
        <span class="sub">{{ cluster }} / {{ system }}</span>
      </div>
      <RouterLink v-if="record" :to="`/jobs/${record.id}`" class="btn">
        View job details &rarr;
      </RouterLink>
    </div>

    <ResultsPanel :cluster="cluster" :system="system" :job-basename="jobBasename" />
  </section>
</template>

<style scoped>
.results-job {
  padding: 1.25rem 1.5rem;
  max-width: 70rem;
}
.back {
  color: var(--text3);
  font-size: var(--t-sm);
  text-decoration: none;
}
.back:hover {
  color: var(--accent);
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin: 0.4rem 0 0.9rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.4rem;
  margin: 0;
  word-break: break-word;
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
}
.btn {
  flex-shrink: 0;
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.4rem 0.9rem;
  font-family: var(--sans);
  font-size: var(--t-sm);
  text-decoration: none;
  white-space: nowrap;
}
.btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
