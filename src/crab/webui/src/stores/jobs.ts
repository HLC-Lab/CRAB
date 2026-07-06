import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { CancelResponse, CrabConfig, JobListItem, JobLogs } from "@/api/types";

const DEFAULT_POLL_INTERVAL_MS = 10_000;
// Status-poll cadence for an in-flight submit/rerun (plan 075) — a genuine
// poll, not a "wait N seconds then reveal" timer: the pending card resolves
// the instant the backend does, whether that's the next tick or the 10th.
const SUBMISSION_POLL_INTERVAL_MS = 1_000;

// A client-only placeholder for a submit/rerun that has been accepted
// (202) but not yet resolved by the background task. Never mixed into
// `items` — the real JobRecord only exists once the backend has one.
export interface PendingSubmission {
  id: string;
  label: string;
  profileName: string;
  status: "pending" | "error";
  errorMessage?: string;
}

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
  const pendingSubmissions = ref<Record<string, PendingSubmission>>({});
  const pendingSubmissionsList = computed(() => Object.values(pendingSubmissions.value));

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

  // Polls one pending submission until it resolves. A plain setInterval per
  // submission (cleared on resolution) rather than one shared timer, since
  // several submits/reruns can be in flight independently.
  function pollSubmission(id: string) {
    const timer = setInterval(async () => {
      let status;
      try {
        status = await api.jobs.submissionStatus(id);
      } catch (e) {
        clearInterval(timer);
        const entry = pendingSubmissions.value[id];
        if (entry)
          pendingSubmissions.value[id] = { ...entry, status: "error", errorMessage: msg(e) };
        return;
      }
      if (status.status === "pending") return;
      clearInterval(timer);
      if (status.status === "done") {
        delete pendingSubmissions.value[id];
        await refresh();
        return;
      }
      const entry = pendingSubmissions.value[id];
      if (entry) {
        pendingSubmissions.value[id] = {
          ...entry,
          status: "error",
          errorMessage: status.detail
            ? `${status.message}\n${status.detail}`
            : (status.message ?? "Submission failed."),
        };
      }
    }, SUBMISSION_POLL_INTERVAL_MS);
  }

  // Returns whether the submit/rerun was accepted (202) — not whether it has
  // finished. The eventual outcome shows up as a pending card (see
  // pendingSubmissionsList) that resolves itself via polling.
  async function submit(body: SubmitBody, label?: string): Promise<boolean> {
    submitBusy.value = true;
    submitError.value = null;
    try {
      const accepted = await api.jobs.submit(body);
      pendingSubmissions.value[accepted.submission_id] = {
        id: accepted.submission_id,
        label: label ?? body.name ?? body.config_id ?? "submission",
        profileName: body.profile_name,
        status: "pending",
      };
      pollSubmission(accepted.submission_id);
      return true;
    } catch (e) {
      submitError.value = msg(e);
      return false;
    } finally {
      submitBusy.value = false;
    }
  }

  function dismissPendingSubmission(id: string) {
    delete pendingSubmissions.value[id];
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
    pendingSubmissionsList,
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
    dismissPendingSubmission,
    cancel,
    fetchLogs,
    openLogs,
    closeLogs,
  };
});
