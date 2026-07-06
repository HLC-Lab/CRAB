<script setup lang="ts">
// Registry ⨝ live crab status (web/api/jobs.py). Auto-poll is a single
// frontend timer (plan 050 design), default on; the store's in-flight guard
// keeps ticks from overlapping.
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { useJobsStore } from "@/stores/jobs";
import ConfirmModal from "@/components/ConfirmModal.vue";
import SubmitJobModal from "@/components/jobs/SubmitJobModal.vue";
import { ansiToHtml } from "@/lib/ansi";
import { isFailureState, isTerminal, stateClass } from "@/lib/jobStatus";
import type { CrabConfig, JobListItem } from "@/api/types";

function filename(path: string): string {
  return path.split("/").pop() || path;
}

const jobs = useJobsStore();
const showSubmit = ref(false);
const cancelTarget = ref<{ id: string; label: string } | null>(null);

const POLL_INTERVAL_OPTIONS = [5_000, 10_000, 30_000, 60_000];

// Drives the "Ns ago" label below — ticks once a second so it stays live
// without needing a fresh refresh.
const now = ref(Date.now());
let nowTimer: ReturnType<typeof setInterval> | null = null;

const lastRefreshedLabel = computed(() => {
  if (jobs.lastRefreshedAt === null) return "never";
  const secs = Math.max(0, Math.round((now.value - jobs.lastRefreshedAt) / 1000));
  if (secs < 1) return "just now";
  if (secs < 60) return `${secs}s ago`;
  return `${Math.round(secs / 60)}m ago`;
});

onMounted(() => {
  jobs.refresh();
  jobs.startPolling();
  nowTimer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});
onUnmounted(() => {
  jobs.stopPolling();
  if (nowTimer) clearInterval(nowTimer);
});

function togglePolling() {
  if (jobs.polling) jobs.stopPolling();
  else jobs.startPolling();
}

function toggleLogs(id: string) {
  if (jobs.openLogId === id) jobs.closeLogs();
  else jobs.openLogs(id);
}

// Keep the log tail in view: jump to the bottom on first open, and stay
// there across refreshes only if the reader hasn't scrolled up to look at
// something earlier (mirrors a `tail -f` follow).
const vStickBottom = {
  mounted(el: HTMLElement) {
    el.scrollTop = el.scrollHeight;
  },
  beforeUpdate(el: HTMLElement & { __wasAtBottom?: boolean }) {
    el.__wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  },
  updated(el: HTMLElement & { __wasAtBottom?: boolean }) {
    if (el.__wasAtBottom) el.scrollTop = el.scrollHeight;
  },
};

function requestCancel(id: string, label: string) {
  cancelTarget.value = { id, label };
}
async function confirmCancel() {
  if (cancelTarget.value) await jobs.cancel(cancelTarget.value.id);
  cancelTarget.value = null;
}

// Rerun resubmits the SAME config_snapshot to the SAME cluster (a fresh
// sbatch submission — Slurm jobs are immutable once run, so "rerun" always
// means a new job, not restarting the old one).
const rerunTarget = ref<{ profile_name: string; config: CrabConfig; name: string } | null>(null);
function requestRerun(j: JobListItem) {
  rerunTarget.value = {
    profile_name: j.cluster,
    config: j.config_snapshot as unknown as CrabConfig,
    name: j.config_name,
  };
}
async function confirmRerun() {
  if (rerunTarget.value) await jobs.submit(rerunTarget.value);
  rerunTarget.value = null;
}

const sortedItems = computed(() => jobs.filteredItems); // already newest-first from the backend

// Filter chip options are drawn from the unfiltered list so a chip never
// disappears just because its own filter narrowed the results to zero.
const availableClusters = computed(() => [...new Set(jobs.items.map((j) => j.cluster))].sort());
const availableStatuses = computed(() =>
  [...new Set(jobs.items.map((j) => j.last_known_state))].sort(),
);

function toggleCluster(name: string) {
  const next = new Set(jobs.clusterFilter);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  jobs.setClusterFilter([...next]);
}
function toggleStatus(name: string) {
  const next = new Set(jobs.statusFilter);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  jobs.setStatusFilter([...next]);
}
</script>

<template>
  <section class="jobs">
    <header class="head">
      <h1>Jobs</h1>
      <div class="actions">
        <span class="meta refreshed-at">refreshed {{ lastRefreshedLabel }}</span>
        <div class="auto-refresh">
          <button
            class="btn auto-refresh-toggle"
            :class="{ on: jobs.polling }"
            @click="togglePolling"
          >
            Auto-refresh: {{ jobs.polling ? "on" : "off" }}
          </button>
          <select
            class="poll-interval"
            :value="jobs.pollIntervalMs"
            @change="jobs.setPollInterval(Number(($event.target as HTMLSelectElement).value))"
          >
            <option v-for="ms in POLL_INTERVAL_OPTIONS" :key="ms" :value="ms">
              {{ ms / 1000 }}s
            </option>
          </select>
        </div>
        <button class="btn" :disabled="jobs.loading" @click="jobs.refresh">
          <span :class="{ spinning: jobs.refreshing }">↻</span> Refresh
        </button>
        <button class="btn primary" @click="showSubmit = true">+ New submission</button>
      </div>
    </header>

    <div v-if="jobs.items.length" class="filters">
      <input
        class="search"
        type="search"
        placeholder="Search use case..."
        :value="jobs.search"
        @input="jobs.setSearch(($event.target as HTMLInputElement).value)"
      />
      <div class="chip-row">
        <span class="chip-label">Cluster:</span>
        <div class="chips">
          <button
            v-for="c in availableClusters"
            :key="c"
            class="chip"
            :class="{ on: jobs.clusterFilter.includes(c) }"
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
            v-for="s in availableStatuses"
            :key="s"
            class="chip"
            :class="{ on: jobs.statusFilter.includes(s) }"
            @click="toggleStatus(s)"
          >
            {{ s }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="jobs.error" class="banner err">{{ jobs.error }}</p>

    <p v-if="!jobs.loading && !sortedItems.length" class="empty">
      {{
        jobs.items.length
          ? "No jobs match the current filters."
          : "No jobs yet. Submit a config to get started."
      }}
    </p>

    <ul class="list">
      <li v-for="j in sortedItems" :key="j.id" class="card">
        <div class="row">
          <div class="ident">
            <span
              class="dot"
              :class="j.connected ? 'on' : 'off'"
              :title="
                j.connected
                  ? 'cluster connected'
                  : 'cluster not connected — showing last known state'
              "
            />
            <RouterLink :to="`/jobs/report/${encodeURIComponent(j.config_name)}`" class="use-case">
              {{ j.config_name }}
            </RouterLink>
            <span class="sub">{{ j.cluster }} · job {{ j.job_id }} · {{ j.system }}</span>
          </div>
          <div class="ctrls">
            <span class="state" :class="stateClass(j.last_known_state)">{{
              j.last_known_state
            }}</span>
            <button class="btn" @click="toggleLogs(j.id)">
              {{ jobs.openLogId === j.id ? "Hide logs" : "Logs" }}
            </button>
            <button
              v-if="!isTerminal(j.last_known_state)"
              class="btn"
              :disabled="jobs.cancelBusy[j.id]"
              @click="requestCancel(j.id, j.config_name)"
            >
              {{ jobs.cancelBusy[j.id] ? "Cancelling…" : "Cancel" }}
            </button>
            <button v-if="isFailureState(j.last_known_state)" class="btn" @click="requestRerun(j)">
              Rerun
            </button>
          </div>
        </div>

        <p class="meta">submitted {{ new Date(j.submitted_at).toLocaleString() }}</p>
        <p v-if="jobs.cancelError[j.id]" class="banner err small">{{ jobs.cancelError[j.id] }}</p>

        <div v-if="jobs.openLogId === j.id" class="logs">
          <p v-if="jobs.logsBusy[j.id] && !jobs.logs[j.id]" class="meta">Loading…</p>
          <p v-else-if="jobs.logsError[j.id]" class="banner err small">
            {{ jobs.logsError[j.id] }}
          </p>
          <template v-else-if="jobs.logs[j.id]">
            <div class="stream">
              <span class="stream-label">{{ filename(jobs.logs[j.id].stdout.path) }}</span>
              <p v-if="jobs.logs[j.id].stdout.truncated" class="meta truncated">
                Showing only the most recent portion — this log is larger than the display limit.
              </p>
              <pre
                v-if="jobs.logs[j.id].stdout.exists && jobs.logs[j.id].stdout.content.trim()"
                v-stick-bottom
                v-html="ansiToHtml(jobs.logs[j.id].stdout.content)"
              ></pre>
              <p v-else-if="jobs.logs[j.id].stdout.exists" class="meta empty">(no output yet)</p>
              <p v-else class="meta">not written yet</p>
            </div>
            <div class="stream">
              <span class="stream-label">{{ filename(jobs.logs[j.id].stderr.path) }}</span>
              <p v-if="jobs.logs[j.id].stderr.truncated" class="meta truncated">
                Showing only the most recent portion — this log is larger than the display limit.
              </p>
              <pre
                v-if="jobs.logs[j.id].stderr.exists && jobs.logs[j.id].stderr.content.trim()"
                v-stick-bottom
                v-html="ansiToHtml(jobs.logs[j.id].stderr.content)"
              ></pre>
              <p v-else-if="jobs.logs[j.id].stderr.exists" class="meta empty">(no output yet)</p>
              <p v-else class="meta">not written yet</p>
            </div>
          </template>
        </div>
      </li>
    </ul>

    <SubmitJobModal v-if="showSubmit" @close="showSubmit = false" @submitted="showSubmit = false" />

    <ConfirmModal
      v-if="cancelTarget"
      title="Cancel this job?"
      :message="`Cancel “${cancelTarget.label}”? This cannot be undone.`"
      confirm-label="Cancel job"
      cancel-label="Keep running"
      @confirm="confirmCancel"
      @cancel="cancelTarget = null"
    />

    <ConfirmModal
      v-if="rerunTarget"
      title="Rerun this use case?"
      :message="`Submit a fresh run of “${rerunTarget.name}” to ${rerunTarget.profile_name}?`"
      confirm-label="Rerun"
      cancel-label="Cancel"
      @confirm="confirmRerun"
      @cancel="rerunTarget = null"
    />
    <p v-if="jobs.submitError" class="banner err small">{{ jobs.submitError }}</p>
  </section>
</template>

<style scoped>
.jobs {
  padding: 1.25rem 1.5rem;
  max-width: 70rem;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.6rem;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.refreshed-at {
  margin: 0;
  white-space: nowrap;
}
.auto-refresh {
  display: flex;
  align-items: center;
}
.auto-refresh-toggle {
  border-radius: var(--r) 0 0 var(--r);
  border-right: none;
}
.poll-interval {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 0 var(--r) var(--r) 0;
  padding: 0.35rem 0.5rem;
  font-family: var(--sans);
  font-size: var(--t-sm);
}
.spinning {
  display: inline-block;
  animation: spin 0.7s linear infinite;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
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
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn.on {
  border-color: var(--ok);
  color: var(--ok);
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
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
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
.banner.small {
  margin-top: 0.5rem;
  font-size: 0.8rem;
}
.empty {
  color: var(--text3);
  padding: 2rem 0;
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
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}
.sub {
  color: var(--text3);
  font-size: var(--t-sm);
}
.use-case {
  color: var(--text);
  font-weight: 600;
  text-decoration: none;
}
.use-case:hover {
  color: var(--accent);
  text-decoration: underline;
}
.ctrls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
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
.meta {
  margin-top: 0.4rem;
  color: var(--text3);
  font-size: var(--t-sm);
}
.meta.empty {
  font-style: italic;
  padding: 0.5rem;
  background: var(--bg2);
  border: 1px dashed var(--border);
  border-radius: var(--r);
}
.meta.truncated {
  margin-bottom: 0.3rem;
  color: var(--warn);
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
/* Matches the SGR codes crab.log.formatters.RichFormatter emits (see lib/ansi.ts). */
.stream pre :deep(.ansi-bold) {
  font-weight: 700;
}
.stream pre :deep(.ansi-dim) {
  color: var(--text3);
}
.stream pre :deep(.ansi-red) {
  color: var(--danger);
}
.stream pre :deep(.ansi-green) {
  color: var(--ok);
}
.stream pre :deep(.ansi-yellow) {
  color: var(--warn);
}
.stream pre :deep(.ansi-blue) {
  color: var(--ansi-blue);
}
.stream pre :deep(.ansi-magenta) {
  color: var(--ansi-magenta);
}
.stream pre :deep(.ansi-cyan) {
  color: var(--ansi-cyan);
}
.stream pre :deep(.ansi-black),
.stream pre :deep(.ansi-white) {
  color: var(--text);
}
</style>
