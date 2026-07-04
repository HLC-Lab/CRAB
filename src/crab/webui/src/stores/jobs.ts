import { defineStore } from "pinia";
import { ref } from "vue";
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

  const logs = ref<Record<string, JobLogs>>({});
  const logsBusy = ref<Record<string, boolean>>({});
  const logsError = ref<Record<string, string>>({});

  async function refresh() {
    if (refreshing.value) return;
    refreshing.value = true;
    loading.value = items.value.length === 0;
    error.value = null;
    try {
      items.value = await api.jobs.list();
      lastRefreshedAt.value = Date.now();
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

  async function fetchLogs(id: string) {
    logsBusy.value[id] = true;
    delete logsError.value[id];
    try {
      logs.value[id] = await api.jobs.logs(id);
    } catch (e) {
      logsError.value[id] = msg(e);
    } finally {
      logsBusy.value[id] = false;
    }
  }

  return {
    items,
    loading,
    error,
    refreshing,
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
    refresh,
    startPolling,
    stopPolling,
    setPollInterval,
    submit,
    cancel,
    fetchLogs,
  };
});
