<script setup lang="ts">
// Per-use-case experiment report (plan 060): every experiment ever run under
// one config name, sourced from `crab history --json` (web/api/jobs.py's
// use_case_report), not just what this dashboard's registry knows about.
import { computed, onMounted } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useReportStore } from "@/stores/report";
import { stateClass } from "@/lib/jobStatus";
import { ansiToHtml } from "@/lib/ansi";

const route = useRoute();
const configName = computed(() => decodeURIComponent(String(route.params.configName)));
const report = useReportStore();

onMounted(() => {
  report.fetchReport(configName.value);
});

function toggleLogs(recordId: string, experimentName: string) {
  report.toggleExperimentLogs(recordId, experimentName);
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

      <ul class="list">
        <li
          v-for="e in report.report.experiments"
          :key="`${e.cluster}/${e.relative_path}`"
          class="card"
        >
          <div class="row">
            <div class="ident">
              <strong>{{ e.experiment_name }}</strong>
              <span class="sub">{{ e.cluster }} / {{ e.system }} · apps: {{ e.apps_list }}</span>
            </div>
            <div class="ctrls">
              <span class="state" :class="stateClass(e.status)">{{ e.status }}</span>
              <button
                v-if="e.record_id"
                class="btn"
                @click="toggleLogs(e.record_id, e.experiment_name)"
              >
                {{
                  report.openExperimentKey === report.experimentKey(e.record_id, e.experiment_name)
                    ? "Hide logs"
                    : "Logs"
                }}
              </button>
              <span v-else class="meta small" title="Not tracked by this dashboard's job registry">
                logs unavailable
              </span>
            </div>
          </div>
          <p class="meta">
            {{ e.timestamp }} · {{ e.numnodes }} node(s) · ppn {{ e.ppn }}
            <span v-if="e.job_id"> · job {{ e.job_id }}</span>
          </p>

          <div
            v-if="
              e.record_id &&
              report.openExperimentKey === report.experimentKey(e.record_id, e.experiment_name)
            "
            class="logs"
          >
            <p
              v-if="report.experimentLogsBusy[report.experimentKey(e.record_id, e.experiment_name)]"
              class="meta"
            >
              Loading…
            </p>
            <p
              v-else-if="
                report.experimentLogsError[report.experimentKey(e.record_id, e.experiment_name)]
              "
              class="banner err small"
            >
              {{ report.experimentLogsError[report.experimentKey(e.record_id, e.experiment_name)] }}
            </p>
            <template
              v-else-if="
                report.experimentLogs[report.experimentKey(e.record_id, e.experiment_name)]
              "
            >
              <p
                v-if="
                  !report.experimentLogs[report.experimentKey(e.record_id, e.experiment_name)].files
                    .length
                "
                class="meta empty"
              >
                No per-app error logs (nothing failed in this experiment).
              </p>
              <div
                v-for="f in report.experimentLogs[
                  report.experimentKey(e.record_id, e.experiment_name)
                ].files"
                :key="f.app_id"
                class="stream"
              >
                <span class="stream-label">app {{ f.app_id }}</span>
                <pre v-html="ansiToHtml(f.content)"></pre>
              </div>
            </template>
          </div>
        </li>
      </ul>
    </template>
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
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 1rem;
  margin-bottom: 0.75rem;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.ident {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
}
.ctrls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}
.state {
  font-family: var(--mono);
  font-size: var(--t-sm);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
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
.meta {
  margin-top: 0.4rem;
  color: var(--text3);
  font-size: var(--t-sm);
}
.meta.small {
  font-size: 0.75rem;
}
.meta.empty {
  font-style: italic;
  padding: 0.5rem;
  background: var(--bg2);
  border: 1px dashed var(--border);
  border-radius: var(--r);
}
.logs {
  margin-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.stream-label {
  display: block;
  color: var(--text2);
  font-size: var(--t-sm);
  margin-bottom: 0.2rem;
}
.stream pre {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.5rem;
  max-height: 16rem;
  overflow: auto;
  font-family: var(--mono);
  font-size: var(--t-sm);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
