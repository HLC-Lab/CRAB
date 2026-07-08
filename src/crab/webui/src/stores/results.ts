import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import { resultsKey } from "@/lib/jobKey";
import type { ExperimentRunStatus, ResultsData, ResultsJobEntry } from "@/api/types";

// Same cadence as jobs.ts's SUBMISSION_POLL_INTERVAL_MS: a genuine poll, not
// a "wait N seconds then reveal" timer.
const FETCH_POLL_INTERVAL_MS = 1_000;

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

export const useResultsStore = defineStore("results", () => {
  // Loaded/cached-state per (cluster, system, jobBasename) -- plan 077 moves
  // job identity off the local registry, so a record id can't key this map
  // (a CLI-only job has none). `notFetched[key]` distinguishes "we asked and
  // there's genuinely nothing cached yet" from "we haven't asked" or "asking
  // failed" — only the first should show a plain "Fetch results" prompt
  // instead of an error.
  const results = ref<Record<string, ResultsData>>({});
  const notFetched = ref<Record<string, boolean>>({});
  const loadBusy = ref<Record<string, boolean>>({});
  const loadError = ref<Record<string, string>>({});

  const fetchBusy = ref<Record<string, boolean>>({});
  const fetchError = ref<Record<string, string>>({});

  // Per-experiment status/run-failure counts (plan 081) -- a separate,
  // registry-independent query from `results`, so it loads even for a job
  // whose CSV tree hasn't been fetched yet.
  const experiments = ref<Record<string, ExperimentRunStatus[]>>({});
  const experimentsBusy = ref<Record<string, boolean>>({});
  const experimentsError = ref<Record<string, string>>({});

  const cacheSize = ref<number | null>(null);
  const cacheSizeLoaded = ref(false);
  const cacheSizeBusy = ref(false);
  const clearBusy = ref(false);
  const clearError = ref<string | null>(null);

  // The picker's cross-cluster index (plan 077 S6/S13) -- every job any
  // connected-or-previously-cached cluster reports, replacing S8's temporary
  // per-job registry loop.
  const index = ref<ResultsJobEntry[]>([]);
  const indexBusy = ref(false);
  const indexError = ref<string | null>(null);
  // Tracks "has a load ever succeeded", independent of `index.value.length`
  // -- an empty jobs list is still a conclusive, cacheable answer.
  const indexLoaded = ref(false);

  // `force=false` (the default) is a no-op once a conclusive answer already
  // exists -- loaded data, or (for loadResults) a confirmed 404 -- so every
  // `onMounted` call site can call these unconditionally without re-hitting
  // the network on a revisit within the same session (plan 079). A prior
  // ERROR is never conclusive: it doesn't block a retry, since staying
  // broken forever after a transient blip would be worse than the extra call.
  async function loadIndex(force = false) {
    if (!force && indexLoaded.value) return;
    indexBusy.value = true;
    indexError.value = null;
    try {
      index.value = (await api.results.index()).jobs;
      indexLoaded.value = true;
    } catch (e) {
      indexError.value = msg(e);
    } finally {
      indexBusy.value = false;
    }
  }

  async function loadResults(cluster: string, system: string, jobBasename: string, force = false) {
    const key = resultsKey(cluster, system, jobBasename);
    if (!force && (key in results.value || notFetched.value[key])) return;
    loadBusy.value[key] = true;
    delete loadError.value[key];
    try {
      results.value[key] = await api.results.get(cluster, system, jobBasename);
      delete notFetched.value[key];
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        notFetched.value[key] = true;
      } else {
        loadError.value[key] = msg(e);
      }
    } finally {
      loadBusy.value[key] = false;
    }
  }

  async function loadExperiments(
    cluster: string,
    system: string,
    jobBasename: string,
    force = false,
  ) {
    const key = resultsKey(cluster, system, jobBasename);
    if (!force && key in experiments.value) return;
    experimentsBusy.value[key] = true;
    delete experimentsError.value[key];
    try {
      experiments.value[key] = (
        await api.results.experiments(cluster, system, jobBasename)
      ).experiments;
    } catch (e) {
      experimentsError.value[key] = msg(e);
    } finally {
      experimentsBusy.value[key] = false;
    }
  }

  // `force=false` is a no-op once already loaded (same convention as
  // `loadIndex`): the total only actually changes after a Fetch or Clear
  // cache, both of which already pass `force=true` -- a plain job-panel visit
  // doesn't need to recompute it (walking the whole on-disk cache) again.
  async function refreshCacheSize(force = false) {
    if (!force && cacheSizeLoaded.value) return;
    cacheSizeBusy.value = true;
    try {
      cacheSize.value = (await api.results.cacheSize()).total_bytes;
      cacheSizeLoaded.value = true;
    } finally {
      cacheSizeBusy.value = false;
    }
  }

  // Polls one in-flight fetch until it resolves — a plain setInterval per
  // fetch (cleared on resolution), same pattern as jobs.ts's pollSubmission,
  // since several jobs' fetches can be in flight independently.
  function pollFetch(cluster: string, system: string, jobBasename: string, fetchId: string) {
    const key = resultsKey(cluster, system, jobBasename);
    const timer = setInterval(async () => {
      let status;
      try {
        status = await api.results.fetchStatus(cluster, system, jobBasename, fetchId);
      } catch (e) {
        clearInterval(timer);
        fetchBusy.value[key] = false;
        fetchError.value[key] = msg(e);
        return;
      }
      if (status.status === "pending") return;
      clearInterval(timer);
      fetchBusy.value[key] = false;
      if (status.status === "done") {
        await loadResults(cluster, system, jobBasename, true); // just fetched -- always reload
        await refreshCacheSize(true);
        return;
      }
      fetchError.value[key] = status.detail
        ? `${status.message}\n${status.detail}`
        : (status.message ?? "Fetch failed.");
    }, FETCH_POLL_INTERVAL_MS);
  }

  async function fetchResults(cluster: string, system: string, jobBasename: string) {
    const key = resultsKey(cluster, system, jobBasename);
    fetchBusy.value[key] = true;
    delete fetchError.value[key];
    try {
      const accepted = await api.results.fetch(cluster, system, jobBasename);
      pollFetch(cluster, system, jobBasename, accepted.fetch_id);
    } catch (e) {
      fetchBusy.value[key] = false;
      fetchError.value[key] = msg(e);
    }
  }

  async function clearCache() {
    clearBusy.value = true;
    clearError.value = null;
    try {
      await api.results.clearCache();
      results.value = {};
      notFetched.value = {};
      await refreshCacheSize(true);
    } catch (e) {
      clearError.value = msg(e);
    } finally {
      clearBusy.value = false;
    }
  }

  return {
    results,
    notFetched,
    loadBusy,
    loadError,
    fetchBusy,
    fetchError,
    experiments,
    experimentsBusy,
    experimentsError,
    cacheSize,
    cacheSizeBusy,
    clearBusy,
    clearError,
    index,
    indexBusy,
    indexError,
    loadIndex,
    loadResults,
    loadExperiments,
    fetchResults,
    refreshCacheSize,
    clearCache,
  };
});
