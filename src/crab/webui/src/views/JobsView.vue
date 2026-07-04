<script setup lang="ts">
// Registry ⨝ live crab status (web/api/jobs.py). Auto-poll is a single
// frontend timer (plan 050 design), default on; the store's in-flight guard
// keeps ticks from overlapping.
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useJobsStore } from "@/stores/jobs";
import ConfirmModal from "@/components/ConfirmModal.vue";
import SubmitJobModal from "@/components/jobs/SubmitJobModal.vue";

// Never re-polled once reached (mirrors api/jobs.py's _TERMINAL_STATES) —
// used here only to decide whether Cancel makes sense to offer.
const TERMINAL_STATES = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "TIMEOUT",
  "OUT_OF_MEMORY",
  "NODE_FAIL",
  "PREEMPTED",
  "BOOT_FAIL",
  "DEADLINE",
  "REVOKED",
]);
function isTerminal(state: string): boolean {
  return TERMINAL_STATES.has(state);
}
function stateClass(state: string): string {
  if (state === "COMPLETED") return "ok";
  if (["FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE"].includes(state))
    return "danger";
  if (state === "CANCELLED" || state === "REVOKED") return "muted";
  if (state === "UNKNOWN") return "warn";
  return "active"; // RUNNING, PENDING, and anything else Slurm reports live
}

const jobs = useJobsStore();
const showSubmit = ref(false);
const expandedLogs = ref<string | null>(null);
const cancelTarget = ref<{ id: string; label: string } | null>(null);

onMounted(() => {
  jobs.refresh();
  jobs.startPolling();
});
onUnmounted(() => {
  jobs.stopPolling();
});

function togglePolling() {
  if (jobs.polling) jobs.stopPolling();
  else jobs.startPolling();
}

async function toggleLogs(id: string) {
  if (expandedLogs.value === id) {
    expandedLogs.value = null;
    return;
  }
  expandedLogs.value = id;
  if (!jobs.logs[id]) await jobs.fetchLogs(id);
}

function requestCancel(id: string, label: string) {
  cancelTarget.value = { id, label };
}
async function confirmCancel() {
  if (cancelTarget.value) await jobs.cancel(cancelTarget.value.id);
  cancelTarget.value = null;
}

const sortedItems = computed(() => jobs.items); // already newest-first from the backend
</script>

<template>
  <section class="jobs">
    <header class="head">
      <h1>Jobs</h1>
      <div class="actions">
        <button class="btn" :disabled="jobs.loading" @click="jobs.refresh()">↻ Refresh</button>
        <button class="btn" :class="{ on: jobs.polling }" @click="togglePolling">
          {{ jobs.polling ? "Auto-refresh: on" : "Auto-refresh: off" }}
        </button>
        <button class="btn primary" @click="showSubmit = true">+ New submission</button>
      </div>
    </header>

    <p v-if="jobs.error" class="banner err">{{ jobs.error }}</p>

    <p v-if="!jobs.loading && !sortedItems.length" class="empty">
      No jobs yet. Submit a config to get started.
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
            <strong>{{ j.config_name }}</strong>
            <span class="sub">{{ j.cluster }} · job {{ j.job_id }} · {{ j.system }}</span>
          </div>
          <div class="ctrls">
            <span class="state" :class="stateClass(j.last_known_state)">{{
              j.last_known_state
            }}</span>
            <button class="btn" @click="toggleLogs(j.id)">
              {{ expandedLogs === j.id ? "Hide logs" : "Logs" }}
            </button>
            <button
              v-if="!isTerminal(j.last_known_state)"
              class="btn"
              :disabled="jobs.cancelBusy[j.id]"
              @click="requestCancel(j.id, j.config_name)"
            >
              {{ jobs.cancelBusy[j.id] ? "Cancelling…" : "Cancel" }}
            </button>
          </div>
        </div>

        <p class="meta">submitted {{ new Date(j.submitted_at).toLocaleString() }}</p>
        <p v-if="jobs.cancelError[j.id]" class="banner err small">{{ jobs.cancelError[j.id] }}</p>

        <div v-if="expandedLogs === j.id" class="logs">
          <p v-if="jobs.logsBusy[j.id]" class="meta">Loading…</p>
          <p v-else-if="jobs.logsError[j.id]" class="banner err small">
            {{ jobs.logsError[j.id] }}
          </p>
          <template v-else-if="jobs.logs[j.id]">
            <div class="stream">
              <span class="stream-label">stdout</span>
              <pre v-if="jobs.logs[j.id].stdout.exists">{{ jobs.logs[j.id].stdout.content }}</pre>
              <p v-else class="meta">not written yet</p>
            </div>
            <div class="stream">
              <span class="stream-label">stderr</span>
              <pre v-if="jobs.logs[j.id].stderr.exists">{{ jobs.logs[j.id].stderr.content }}</pre>
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
  gap: 0.5rem;
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
