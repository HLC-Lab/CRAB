<script setup lang="ts">
// Per-job detail view (plan 075): every experiment from this exact
// submission, sourced from web/api/jobs.py's job_experiments (not the whole
// use-case history — see ReportView.vue for that secondary view, linked
// below). Reuses ExperimentCard.vue and useReportStore for the shared
// per-experiment logs/selection state; useJobsStore only for looking up a
// config_snapshot when rerunning selected experiments.
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useJobDetailStore } from "@/stores/jobDetail";
import { useReportStore } from "@/stores/report";
import { useJobsStore } from "@/stores/jobs";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ExperimentCard from "@/components/jobs/ExperimentCard.vue";
import RerunSummaryCard from "@/components/jobs/RerunSummaryCard.vue";
import type { CrabConfig } from "@/api/types";

const route = useRoute();
const recordId = computed(() => String(route.params.recordId));
const detailStore = useJobDetailStore();
const report = useReportStore();
const jobs = useJobsStore();

onMounted(() => {
  detailStore.fetchDetail(recordId.value);
  jobs.refresh(); // needed to look up a config_snapshot when rerunning selected experiments
});

const selectedCount = computed(() => report.selected.size);
const canRerunSelected = computed(
  () => report.selectedRecordIds.size === 1 && selectedCount.value > 0,
);
const showRerunConfirm = ref(false);
const rerunLookupError = ref<string | null>(null);
async function confirmRerunSelected() {
  showRerunConfirm.value = false;
  rerunLookupError.value = null;
  const [selectedRecordId] = report.selectedRecordIds;
  const rec = jobs.items.find((j) => j.id === selectedRecordId);
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
    rerun_of: rec.id,
  });
  report.exitSelectionMode();
}
</script>

<template>
  <section class="detail">
    <RouterLink to="/jobs" class="back">&larr; Jobs</RouterLink>

    <p v-if="detailStore.loading" class="meta">Loading…</p>
    <p v-else-if="detailStore.error" class="banner err">{{ detailStore.error }}</p>

    <template v-else-if="detailStore.detail">
      <div class="head">
        <h1>{{ detailStore.detail.config_name }}</h1>
        <span class="sub">
          {{ detailStore.detail.cluster }} / {{ detailStore.detail.system }} · job
          {{ detailStore.detail.job_id }} · submitted
          {{ new Date(detailStore.detail.submitted_at).toLocaleString() }}
        </span>
      </div>

      <p v-if="detailStore.detail.stale" class="banner warn">
        Showing cached data from
        {{ new Date(detailStore.detail.cached_at as string).toLocaleString() }},
        {{ detailStore.detail.cluster }} is unreachable.
      </p>

      <p v-if="detailStore.detail.rerun_of" class="banner rerun-of">
        Rerun of
        <RouterLink :to="`/jobs/${detailStore.detail.rerun_of.id}`">{{
          detailStore.detail.rerun_of.config_name
        }}</RouterLink>
        submitted {{ new Date(detailStore.detail.rerun_of.submitted_at).toLocaleString() }}
        · reran:
        {{
          detailStore.detail.rerun_experiments?.length
            ? detailStore.detail.rerun_experiments.join(", ")
            : "all experiments"
        }}
      </p>

      <RouterLink
        :to="`/jobs/report/${encodeURIComponent(detailStore.detail.config_name)}`"
        class="history-link"
      >
        View full history for this use case &rarr;
      </RouterLink>

      <p v-if="!detailStore.detail.experiments.length" class="empty">
        No experiments found for this submission.
      </p>

      <button
        v-if="detailStore.detail.experiments.length"
        class="btn select-toggle"
        @click="report.toggleSelectionMode"
      >
        {{ report.selectionMode ? "Cancel selection" : "Select experiments to rerun…" }}
      </button>

      <div v-if="report.selectionMode" class="rerun-bar">
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
          v-for="e in detailStore.detail.experiments"
          :key="`${e.cluster}/${e.relative_path}`"
          :experiment="e"
        />
      </ul>

      <section v-if="detailStore.detail.reruns.length" class="reruns">
        <h2>Reruns ({{ detailStore.detail.reruns.length }})</h2>
        <ul class="list">
          <RerunSummaryCard v-for="r in detailStore.detail.reruns" :key="r.id" :job="r" />
        </ul>
      </section>
    </template>

    <ConfirmModal
      v-if="showRerunConfirm"
      title="Rerun selected experiments?"
      :message="`Submit a fresh run of ${selectedCount} experiment(s)?`"
      confirm-label="Rerun"
      cancel-label="Cancel"
      @confirm="confirmRerunSelected"
      @cancel="showRerunConfirm = false"
    />
  </section>
</template>

<style scoped>
.detail {
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
  margin: 0.4rem 0 0.6rem;
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
.history-link {
  display: inline-block;
  color: var(--accent);
  font-size: var(--t-sm);
  text-decoration: none;
  margin-bottom: 1rem;
}
.history-link:hover {
  text-decoration: underline;
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
.banner.rerun-of {
  background: var(--bg1);
  border: 1px solid var(--border);
  color: var(--text2);
  font-size: var(--t-sm);
}
.banner.rerun-of a {
  color: var(--accent);
}
.reruns {
  margin-top: 1.5rem;
}
.reruns h2 {
  font-family: var(--sans);
  font-size: 1rem;
  margin-bottom: 0.6rem;
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
.select-toggle {
  margin-bottom: 0.75rem;
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
