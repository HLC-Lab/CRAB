<script setup lang="ts">
// One experiment row from a report (per-use-case history or, later, a
// per-job detail view): status, per-app logs toggle, rerun selection.
// Extracted from ReportView.vue (plan 075 S1) so a future per-job view can
// reuse the same card without duplicating this markup.
import { useReportStore } from "@/stores/report";
import { stateClass } from "@/lib/jobStatus";
import { ansiToHtml } from "@/lib/ansi";
import type { ReportExperiment } from "@/api/types";

defineProps<{ experiment: ReportExperiment }>();

const report = useReportStore();

function toggleLogs(recordId: string, experimentName: string) {
  report.toggleExperimentLogs(recordId, experimentName);
}
</script>

<template>
  <li class="card">
    <div class="row">
      <div class="ident">
        <label class="pick">
          <input
            v-if="experiment.record_id"
            type="checkbox"
            :checked="
              report.selected.has(
                report.experimentKey(experiment.record_id, experiment.experiment_name),
              )
            "
            @change="report.toggleSelected(experiment.record_id, experiment.experiment_name)"
          />
          <strong>{{ experiment.experiment_name }}</strong>
        </label>
        <span class="sub">
          {{ experiment.cluster }} / {{ experiment.system }} · apps: {{ experiment.apps_list }}
        </span>
      </div>
      <div class="ctrls">
        <span class="state" :class="stateClass(experiment.status)">{{ experiment.status }}</span>
        <button
          v-if="experiment.record_id"
          class="btn"
          @click="toggleLogs(experiment.record_id, experiment.experiment_name)"
        >
          {{
            report.openExperimentKey ===
            report.experimentKey(experiment.record_id, experiment.experiment_name)
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
      {{ experiment.timestamp }} · {{ experiment.numnodes }} node(s) · ppn {{ experiment.ppn }}
      <span v-if="experiment.job_id"> · job {{ experiment.job_id }}</span>
    </p>

    <div
      v-if="
        experiment.record_id &&
        report.openExperimentKey ===
          report.experimentKey(experiment.record_id, experiment.experiment_name)
      "
      class="logs"
    >
      <p
        v-if="
          report.experimentLogsBusy[
            report.experimentKey(experiment.record_id, experiment.experiment_name)
          ]
        "
        class="meta"
      >
        Loading…
      </p>
      <p
        v-else-if="
          report.experimentLogsError[
            report.experimentKey(experiment.record_id, experiment.experiment_name)
          ]
        "
        class="banner err small"
      >
        {{
          report.experimentLogsError[
            report.experimentKey(experiment.record_id, experiment.experiment_name)
          ]
        }}
      </p>
      <template
        v-else-if="
          report.experimentLogs[
            report.experimentKey(experiment.record_id, experiment.experiment_name)
          ]
        "
      >
        <p
          v-if="
            report.experimentLogs[
              report.experimentKey(experiment.record_id, experiment.experiment_name)
            ].stale
          "
          class="banner warn small"
        >
          Showing cached data from
          {{
            new Date(
              report.experimentLogs[
                report.experimentKey(experiment.record_id, experiment.experiment_name)
              ].cached_at as string,
            ).toLocaleString()
          }}, {{ experiment.cluster }} is unreachable.
        </p>
        <p
          v-if="
            !report.experimentLogs[
              report.experimentKey(experiment.record_id, experiment.experiment_name)
            ].files.length
          "
          class="meta empty"
        >
          No per-app error logs (nothing failed in this experiment).
        </p>
        <div
          v-for="f in report.experimentLogs[
            report.experimentKey(experiment.record_id, experiment.experiment_name)
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
</template>

<style scoped>
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
.pick {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
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
