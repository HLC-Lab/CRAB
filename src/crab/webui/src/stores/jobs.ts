import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { CancelResponse, CrabConfig, JobListItem, JobLogs, JobRecord } from "@/api/types";

const DEFAULT_POLL_INTERVAL_MS = 10_000;

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  // `detail` usually carries the remote stderr/stdout snippet (e.g. why a
  // `crab run` failed on the cluster) — without it the message alone
  // ("failed on the cluster (exit 1)") gives no way to diagnose anything.
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

export interface SubmitBody {
  profile_name: string;
  config_id?: string;
  config?: CrabConfig;
  name?: string;
  preset?: string;
  only?: string[];
}

export const useJobsStore = defineStore("jobs", () => {
  const items = ref<JobListItem[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  // In-flight guard: a poll tick that lands while the previous one is still
  // running is skipped rather than queued, so overlapping requests can't pile up.
  const refreshing = ref(false);

  const polling = ref(false);
  const lastRefreshedAt = ref<number | null>(null);
  const pollIntervalMs = ref(DEFAULT_POLL_INTERVAL_MS);
  let timer: ReturnType<typeof setInterval> | null = null;

  const submitBusy = ref(false);
  const submitError = ref<string | null>(null);

  const cancelBusy = ref<Record<string, boolean>>({});
  const cancelError = ref<Record<string, string>>({});

  // Jobs view filters (plan 060): all client-side over the already-fetched
  // list, no extra API calls. An empty selection/string means "no filter".
  const clusterFilter = ref<string[]>([]);
  const statusFilter = ref<string[]>([]);
  const search = ref("");

  const filteredItems = computed(() =>
    items.value.filter((j) => {
      if (clusterFilter.value.length && !clusterFilter.value.includes(j.cluster)) return false;
      if (statusFilter.value.length && !statusFilter.value.includes(j.last_known_state))
        return false;
      if (search.value && !j.config_name.toLowerCase().includes(search.value.toLowerCase()))
        return false;
      return true;
    }),
  );

  function setClusterFilter(clusters: string[]) {
    clusterFilter.value = clusters;
  }
  function setStatusFilter(statuses: string[]) {
    statusFilter.value = statuses;
  }
  function setSearch(query: string) {
    search.value = query;
  }

  const logs = ref<Record<string, JobLogs>>({});
  const logsBusy = ref<Record<string, boolean>>({});
  const logsError = ref<Record<string, string>>({});
  // The one log panel a user can have open at a time. Owned here (not by the
  // view) so both manual refresh AND the poll timer keep it live — a job
  // watched while it's still running needs its tail to actually update.
  const openLogId = ref<string | null>(null);

  async function fetchLogs(id: string) {
    // Only flip logsBusy to show a loading state on the FIRST fetch: once
    // content exists, a background refresh should update it in place, not
    // blank the panel out and back in (destroys/recreates the DOM for no
    // reason, and loses the reader's scroll position).
    if (!logs.value[id]) logsBusy.value[id] = true;
    delete logsError.value[id];
    try {
      logs.value[id] = await api.jobs.logs(id);
    } catch (e) {
      logsError.value[id] = msg(e);
    } finally {
      logsBusy.value[id] = false;
    }
  }

  function openLogs(id: string) {
    openLogId.value = id;
    return fetchLogs(id);
  }

  function closeLogs() {
    openLogId.value = null;
  }

  async function refresh() {
    if (refreshing.value) return;
    refreshing.value = true;
    loading.value = items.value.length === 0;
    error.value = null;
    try {
      items.value = await api.jobs.list();
      lastRefreshedAt.value = Date.now();
      if (openLogId.value) await fetchLogs(openLogId.value);
    } catch (e) {
      error.value = msg(e);
    } finally {
      loading.value = false;
      refreshing.value = false;
    }
  }

  function startPolling() {
    if (timer !== null) return;
    polling.value = true;
    timer = setInterval(refresh, pollIntervalMs.value);
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    polling.value = false;
  }

  function setPollInterval(ms: number) {
    pollIntervalMs.value = ms;
    if (timer !== null) {
      clearInterval(timer);
      timer = setInterval(refresh, ms);
    }
  }

  async function submit(body: SubmitBody): Promise<JobRecord | null> {
    submitBusy.value = true;
    submitError.value = null;
    try {
      const rec = await api.jobs.submit(body);
      await refresh();
      return rec;
    } catch (e) {
      submitError.value = msg(e);
      return null;
    } finally {
      submitBusy.value = false;
    }
  }

  async function cancel(id: string): Promise<CancelResponse | null> {
    cancelBusy.value[id] = true;
    delete cancelError.value[id];
    try {
      const res = await api.jobs.cancel(id);
      // A 200 with cancelled: false isn't a request failure (e.g. the job was
      // already gone) — surface `detail` the same way an error would be shown,
      // since silently doing nothing would look like the click had no effect.
      if (!res.cancelled) {
        cancelError.value[id] = res.detail || "Could not cancel this job.";
      }
      await refresh();
      return res;
    } catch (e) {
      cancelError.value[id] = msg(e);
      return null;
    } finally {
      cancelBusy.value[id] = false;
    }
  }

  return {
    items,
    loading,
    error,
    refreshing,
    clusterFilter,
    statusFilter,
    search,
    filteredItems,
    setClusterFilter,
    setStatusFilter,
    setSearch,
    polling,
    lastRefreshedAt,
    pollIntervalMs,
    submitBusy,
    submitError,
    cancelBusy,
    cancelError,
    logs,
    logsBusy,
    logsError,
    openLogId,
    refresh,
    startPolling,
    stopPolling,
    setPollInterval,
    submit,
    cancel,
    fetchLogs,
    openLogs,
    closeLogs,
  };
});
