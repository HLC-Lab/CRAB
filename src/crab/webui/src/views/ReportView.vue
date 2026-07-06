<script setup lang="ts">
// Per-use-case experiment report (plan 060): every experiment ever run under
// one config name, sourced from `crab history --json` (web/api/jobs.py's
// use_case_report), not just what this dashboard's registry knows about.
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useReportStore } from "@/stores/report";
import { useJobsStore } from "@/stores/jobs";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ExperimentCard from "@/components/jobs/ExperimentCard.vue";
import { groupExperimentsBySubmission } from "@/lib/groupExperimentsBySubmission";
import { failedExperimentNames } from "@/lib/jobStatus";
import type { CrabConfig } from "@/api/types";

const route = useRoute();
const configName = computed(() => decodeURIComponent(String(route.params.configName)));
const report = useReportStore();
const jobs = useJobsStore();

onMounted(() => {
  report.fetchReport(configName.value);
  jobs.refresh(); // needed to look up a config_snapshot when rerunning selected experiments
});

// Grouped by submission (plan 076) instead of one flat list; only the most
// recent submission starts expanded, since a use case can span many.
const groups = computed(() =>
  report.report ? groupExperimentsBySubmission(report.report.experiments) : [],
);
const expandedKeys = ref<Set<string>>(new Set());
watch(groups, (gs) => {
  if (gs.length) expandedKeys.value = new Set([gs[0].key]);
});
function toggleGroup(key: string) {
  const next = new Set(expandedKeys.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedKeys.value = next;
}

// "Rerun selected" only makes sense within one job submission at a time: that's
// the only thing carrying a config_snapshot to resubmit.
const selectedCount = computed(() => report.selected.size);
const canRerunSelected = computed(
  () => report.selectedRecordIds.size === 1 && selectedCount.value > 0,
);
const rerunLookupError = ref<string | null>(null);
async function submitRerun(targetRecordId: string, experimentNames: string[]) {
  rerunLookupError.value = null;
  const rec = jobs.items.find((j) => j.id === targetRecordId);
  if (!rec) {
    rerunLookupError.value =
      "Could not find this job's details to rerun — try refreshing the Jobs page.";
    return;
  }
  await jobs.submit({
    profile_name: rec.cluster,
    config: rec.config_snapshot as unknown as CrabConfig,
    name: rec.config_name,
    only: experimentNames,
    rerun_of: rec.id,
  });
}

const showRerunConfirm = ref(false);
async function confirmRerunSelected() {
  showRerunConfirm.value = false;
  const [selectedRecordId] = report.selectedRecordIds;
  await submitRerun(
    selectedRecordId,
    [...report.selected].map((key) => key.split("/")[1]),
  );
  report.exitSelectionMode();
}

// One-click rerun for the common case: retry exactly a group's failed
// experiments, no selection step (plan 076). Only meaningful for a group
// with a known record_id (a manual run has no config_snapshot to resubmit).
function failedNamesFor(group: {
  recordId: string | null;
  experiments: { status: string; experiment_name: string }[];
}) {
  if (!group.recordId) return [];
  return failedExperimentNames(group.experiments);
}
const rerunFailedTarget = ref<{ recordId: string; names: string[] } | null>(null);
async function confirmRerunFailed() {
  if (rerunFailedTarget.value) {
    await submitRerun(rerunFailedTarget.value.recordId, rerunFailedTarget.value.names);
  }
  rerunFailedTarget.value = null;
}
</script>

<template>
  <section class="report">
    <RouterLink to="/jobs" class="back">&larr; Jobs</RouterLink>
    <h1>{{ configName }}</h1>
    <p class="intro">Every run of this use case, across every connected cluster and over time.</p>

    <p v-if="report.loading" class="meta">Loading…</p>
    <p v-else-if="report.error" class="banner err">{{ report.error }}</p>

    <template v-else-if="report.report">
      <p v-if="report.report.clusters_skipped.length" class="banner warn">
        Not connected, so skipped: {{ report.report.clusters_skipped.join(", ") }}. Connect
        {{ report.report.clusters_skipped.length > 1 ? "these clusters" : "this cluster" }} to see
        their history too.
      </p>

      <p v-for="s in report.report.clusters_stale" :key="s.cluster" class="banner warn">
        Showing cached data from {{ new Date(s.cached_at).toLocaleString() }}, {{ s.cluster }} is
        unreachable.
      </p>

      <p v-if="!report.report.experiments.length" class="empty">
        No experiments found for this use case on any connected cluster.
      </p>

      <button
        v-if="report.report.experiments.length"
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

      <ul class="groups">
        <li v-for="g in groups" :key="g.key" class="group">
          <div class="group-head">
            <button class="group-toggle" @click="toggleGroup(g.key)">
              <span class="chevron" :class="{ open: expandedKeys.has(g.key) }">&rsaquo;</span>
              <span class="group-title">
                {{ g.cluster }} / {{ g.system }} · {{ new Date(g.submittedAt).toLocaleString() }}
              </span>
              <span class="meta small">{{ g.experiments.length }} experiment(s)</span>
            </button>
            <button
              v-if="failedNamesFor(g).length"
              class="btn small"
              @click="
                rerunFailedTarget = { recordId: g.recordId as string, names: failedNamesFor(g) }
              "
            >
              Rerun {{ failedNamesFor(g).length }} failed
            </button>
          </div>
          <ul v-if="expandedKeys.has(g.key)" class="list">
            <ExperimentCard
              v-for="e in g.experiments"
              :key="`${e.cluster}/${e.relative_path}`"
              :experiment="e"
            />
          </ul>
        </li>
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
    <ConfirmModal
      v-if="rerunFailedTarget"
      title="Rerun failed experiments?"
      :message="`Submit a fresh run of ${rerunFailedTarget.names.length} failed experiment(s): ${rerunFailedTarget.names.join(', ')}?`"
      confirm-label="Rerun"
      cancel-label="Cancel"
      @confirm="confirmRerunFailed"
      @cancel="rerunFailedTarget = null"
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
  margin: 0.4rem 0 0.3rem;
  word-break: break-word;
}
.intro {
  color: var(--text3);
  font-size: var(--t-sm);
  margin: 0 0 1rem;
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
.list,
.groups {
  list-style: none;
}
.group {
  margin-bottom: 0.75rem;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.6rem 0.85rem;
}
.group-toggle {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  background: none;
  border: none;
  cursor: pointer;
  font-family: var(--sans);
  color: var(--text);
  text-align: left;
  padding: 0;
}
.group-head:hover {
  border-color: var(--accent);
}
.btn.small {
  padding: 0.25rem 0.6rem;
  font-size: var(--t-sm);
}
.chevron {
  display: inline-block;
  transition: transform 0.15s;
  color: var(--text3);
}
.chevron.open {
  transform: rotate(90deg);
}
.group-title {
  flex: 1;
  font-weight: 600;
}
.group .list {
  margin-top: 0.5rem;
  padding-left: 0.5rem;
}
.select-toggle {
  margin-bottom: 0.75rem;
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
