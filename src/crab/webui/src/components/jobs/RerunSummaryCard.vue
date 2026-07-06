<script setup lang="ts">
// One rerun of a job, summarized (plan 076) — used in a job detail view's
// "Reruns" section. Deliberately not ExperimentCard: this summarizes a whole
// job record, not one experiment row.
import { RouterLink } from "vue-router";
import { stateClass } from "@/lib/jobStatus";
import type { JobRecord } from "@/api/types";

defineProps<{ job: JobRecord }>();
</script>

<template>
  <li class="card">
    <RouterLink :to="`/jobs/${job.id}`" class="link">
      <span class="sub">submitted {{ new Date(job.submitted_at).toLocaleString() }}</span>
      <span class="row">
        <span class="experiments">{{
          job.rerun_experiments?.length ? job.rerun_experiments.join(", ") : "all experiments"
        }}</span>
        <span class="state" :class="stateClass(job.last_known_state)">{{
          job.last_known_state
        }}</span>
      </span>
    </RouterLink>
  </li>
</template>

<style scoped>
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.6rem 0.85rem;
  margin-bottom: 0.5rem;
}
.link {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  text-decoration: none;
  color: var(--text);
}
.link:hover .experiments {
  color: var(--accent);
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.experiments {
  font-family: var(--mono);
  font-size: var(--t-sm);
}
.state {
  font-family: var(--mono);
  font-size: var(--t-sm);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  flex-shrink: 0;
}
.state.ok {
  color: var(--ok);
  border-color: var(--ok);
}
.state.danger {
  color: var(--danger);
  border-color: var(--danger);
}
.state.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.state.active {
  color: var(--accent);
  border-color: var(--accent);
}
.state.muted {
  color: var(--text3);
}
</style>
