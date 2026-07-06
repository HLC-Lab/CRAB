<script setup lang="ts">
// Per-use-case experiment report (plan 060): every experiment ever run under
// one config name, sourced from `crab history --json` (web/api/jobs.py's
// use_case_report), not just what this dashboard's registry knows about.
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useReportStore } from "@/stores/report";
import { useJobsStore } from "@/stores/jobs";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ExperimentCard from "@/components/jobs/ExperimentCard.vue";
import type { CrabConfig } from "@/api/types";

const route = useRoute();
const configName = computed(() => decodeURIComponent(String(route.params.configName)));
const report = useReportStore();
const jobs = useJobsStore();

onMounted(() => {
  report.fetchReport(configName.value);
  jobs.refresh(); // needed to look up a config_snapshot when rerunning selected experiments
});

// "Rerun selected" only makes sense within one job submission at a time: that's
// the only thing carrying a config_snapshot to resubmit.
const selectedCount = computed(() => report.selected.size);
const canRerunSelected = computed(
  () => report.selectedRecordIds.size === 1 && selectedCount.value > 0,
);
const showRerunConfirm = ref(false);
const rerunLookupError = ref<string | null>(null);
async function confirmRerunSelected() {
  showRerunConfirm.value = false;
  rerunLookupError.value = null;
  const [recordId] = report.selectedRecordIds;
  const rec = jobs.items.find((j) => j.id === recordId);
  if (!rec) {
    rerunLookupError.value =
      "Could not find this job's details to rerun — try refreshing the Jobs page.";
    return;
  }
  const experimentNames = [...report.selected].map((key) => key.split("/")[1]);
  await jobs.submit({
    profile_name: rec.cluster,
    config: rec.config_snapshot as unknown as CrabConfig,
    name: rec.config_name,
    only: experimentNames,
  });
  report.clearSelected();
}
</script>

<template>
  <section class="report">
    <RouterLink to="/jobs" class="back">&larr; Jobs</RouterLink>
    <h1>{{ configName }}</h1>

    <p v-if="report.loading" class="meta">Loading…</p>
    <p v-else-if="report.error" class="banner err">{{ report.error }}</p>

    <template v-else-if="report.report">
      <p v-if="report.report.clusters_skipped.length" class="banner warn">
        Not connected, so skipped: {{ report.report.clusters_skipped.join(", ") }}. Connect
        {{ report.report.clusters_skipped.length > 1 ? "these clusters" : "this cluster" }} to see
        their history too.
      </p>

      <p v-if="!report.report.experiments.length" class="empty">
        No experiments found for this use case on any connected cluster.
      </p>

      <div v-if="selectedCount" class="rerun-bar">
        <span>{{ selectedCount }} experiment(s) selected</span>
        <span v-if="!canRerunSelected" class="meta small">
          Select experiments from a single job submission to rerun them together.
        </span>
        <button class="btn" @click="report.clearSelected">Clear</button>
        <button class="btn primary" :disabled="!canRerunSelected" @click="showRerunConfirm = true">
          Rerun selected
        </button>
      </div>
      <p v-if="rerunLookupError" class="banner err small">{{ rerunLookupError }}</p>
      <p v-if="jobs.submitError" class="banner err small">{{ jobs.submitError }}</p>

      <ul class="list">
        <ExperimentCard
          v-for="e in report.report.experiments"
          :key="`${e.cluster}/${e.relative_path}`"
          :experiment="e"
        />
      </ul>
    </template>

    <ConfirmModal
      v-if="showRerunConfirm"
      title="Rerun selected experiments?"
      :message="`Submit a fresh run of ${selectedCount} experiment(s) from “${configName}”?`"
      confirm-label="Rerun"
      cancel-label="Cancel"
      @confirm="confirmRerunSelected"
      @cancel="showRerunConfirm = false"
    />
  </section>
</template>

<style scoped>
.report {
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
h1 {
  font-family: var(--sans);
  font-size: 1.4rem;
  margin: 0.4rem 0 1rem;
  word-break: break-word;
}
.banner {
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
  margin-bottom: 1rem;
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
  white-space: pre-wrap;
}
.banner.warn {
  background: rgba(237, 137, 54, 0.12);
  color: var(--warn);
  border: 1px solid var(--warn);
}
.banner.small {
  margin-top: 0.5rem;
  font-size: 0.8rem;
}
.empty {
  color: var(--text3);
  padding: 1rem 0;
}
.list {
  list-style: none;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
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
.rerun-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.6rem 1rem;
  margin-bottom: 1rem;
}
.meta {
  margin-top: 0.4rem;
  color: var(--text3);
  font-size: var(--t-sm);
}
.meta.small {
  font-size: 0.75rem;
}
</style>
