/**
 * Fetch/poll/cache-size/clear behavior for the results store (plan 077 re-key:
 * (cluster, system, jobBasename) instead of a registry record id), mirroring
 * jobs.store.spec.ts's async-submit fake-timer technique. Mocks the API client, the
 * true I/O boundary, not store internals.
 */
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    results: {
      index: vi.fn(),
      fetch: vi.fn(),
      fetchStatus: vi.fn(),
      get: vi.fn(),
      experiments: vi.fn(),
      cacheSize: vi.fn(),
      clearCache: vi.fn(),
    },
  },
  ApiError: class ApiError extends Error {
    detail?: string;
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  },
}));

import { ApiError, api } from "@/api/client";
import { useResultsStore } from "@/stores/results";

const indexMock = vi.mocked(api.results.index);
const fetchMock = vi.mocked(api.results.fetch);
const fetchStatusMock = vi.mocked(api.results.fetchStatus);
const getMock = vi.mocked(api.results.get);
const experimentsMock = vi.mocked(api.results.experiments);
const cacheSizeMock = vi.mocked(api.results.cacheSize);
const clearCacheMock = vi.mocked(api.results.clearCache);

const CLUSTER = "leonardo";
const SYSTEM = "leonardo";
const JOB_BASENAME = "demo_job";
const KEY = `${CLUSTER}/${SYSTEM}/${JOB_BASENAME}`;

const SAMPLE_DATA = { experiments: { Root: { "App 0": [{ x: 1 }] } } };

beforeEach(() => {
  setActivePinia(createPinia());
  indexMock.mockReset();
  fetchMock.mockReset();
  fetchStatusMock.mockReset();
  getMock.mockReset();
  experimentsMock.mockReset();
  cacheSizeMock.mockReset();
  clearCacheMock.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("results store: loading cached data", () => {
  it("marks a job as not-yet-fetched on a 404", async () => {
    getMock.mockRejectedValueOnce(new ApiError("No results cached yet.", 404));

    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.notFetched[KEY]).toBe(true);
    expect(store.results[KEY]).toBeUndefined();
    expect(store.loadError[KEY]).toBeUndefined();
  });

  it("stores the parsed data on success", async () => {
    getMock.mockResolvedValueOnce(SAMPLE_DATA);

    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.results[KEY]).toEqual(SAMPLE_DATA);
    expect(store.notFetched[KEY]).toBeUndefined();
  });

  it("surfaces a non-404 failure as a load error, not not-fetched", async () => {
    getMock.mockRejectedValueOnce(new ApiError("Cannot reach the dashboard backend.", 0));

    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.notFetched[KEY]).toBeUndefined();
    expect(store.loadError[KEY]).toBe("Cannot reach the dashboard backend.");
  });

  it("skips a second unforced call once the data is already loaded (plan 079)", async () => {
    getMock.mockResolvedValueOnce(SAMPLE_DATA);
    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it("skips a second unforced call once a 404 is already confirmed (plan 079)", async () => {
    getMock.mockRejectedValueOnce(new ApiError("No results cached yet.", 404));
    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it("force=true always calls through even when already loaded (plan 079)", async () => {
    getMock.mockResolvedValue(SAMPLE_DATA);
    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME, true);

    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it("a prior load error still allows an unforced retry (plan 079)", async () => {
    getMock.mockRejectedValueOnce(new ApiError("Cannot reach the dashboard backend.", 0));
    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);
    expect(store.loadError[KEY]).toBeDefined();

    getMock.mockResolvedValueOnce(SAMPLE_DATA);
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(store.results[KEY]).toEqual(SAMPLE_DATA);
  });
});

describe("results store: loadExperiments (plan 081)", () => {
  const SAMPLE_EXPERIMENTS = [
    { experiment_name: "01_baseline", status: "FAILED", total_runs: "10", failed_runs: "3" },
  ];

  it("stores the returned experiment list on success", async () => {
    experimentsMock.mockResolvedValueOnce({ experiments: SAMPLE_EXPERIMENTS });

    const store = useResultsStore();
    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.experiments[KEY]).toEqual(SAMPLE_EXPERIMENTS);
    expect(store.experimentsError[KEY]).toBeUndefined();
  });

  it("surfaces a failure as an experiments load error", async () => {
    experimentsMock.mockRejectedValueOnce(new ApiError("Cannot reach the dashboard backend.", 0));

    const store = useResultsStore();
    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.experiments[KEY]).toBeUndefined();
    expect(store.experimentsError[KEY]).toBe("Cannot reach the dashboard backend.");
  });

  it("skips a second unforced call once already loaded", async () => {
    experimentsMock.mockResolvedValueOnce({ experiments: SAMPLE_EXPERIMENTS });
    const store = useResultsStore();
    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME);

    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(experimentsMock).toHaveBeenCalledTimes(1);
  });

  it("force=true always calls through even when already loaded", async () => {
    experimentsMock.mockResolvedValue({ experiments: SAMPLE_EXPERIMENTS });
    const store = useResultsStore();
    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME);

    await store.loadExperiments(CLUSTER, SYSTEM, JOB_BASENAME, true);

    expect(experimentsMock).toHaveBeenCalledTimes(2);
  });
});

describe("results store: fetch + poll", () => {
  it("polls until done, then loads the data and refreshes cache size", async () => {
    fetchMock.mockResolvedValue({ fetch_id: "fetch-1" });
    fetchStatusMock.mockResolvedValueOnce({ status: "pending" });
    fetchStatusMock.mockResolvedValueOnce({ status: "done" });
    getMock.mockResolvedValue(SAMPLE_DATA);
    cacheSizeMock.mockResolvedValue({ total_bytes: 42 });

    const store = useResultsStore();
    await store.fetchResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.fetchBusy[KEY]).toBe(true);

    await vi.advanceTimersByTimeAsync(1_000); // first poll: still pending
    expect(store.fetchBusy[KEY]).toBe(true);

    await vi.advanceTimersByTimeAsync(1_000); // second poll: done
    expect(store.fetchBusy[KEY]).toBe(false);
    expect(store.results[KEY]).toEqual(SAMPLE_DATA);
    expect(store.cacheSize).toBe(42);
  });

  it("keeps message+detail on an error resolution", async () => {
    fetchMock.mockResolvedValue({ fetch_id: "fetch-1" });
    fetchStatusMock.mockResolvedValueOnce({
      status: "error",
      message: "Could not fetch results.",
      detail: "boom",
    });

    const store = useResultsStore();
    await store.fetchResults(CLUSTER, SYSTEM, JOB_BASENAME);
    await vi.advanceTimersByTimeAsync(1_000);

    expect(store.fetchBusy[KEY]).toBe(false);
    expect(store.fetchError[KEY]).toBe("Could not fetch results.\nboom");
  });

  it("surfaces a failure to even start the fetch (e.g. not connected)", async () => {
    fetchMock.mockRejectedValueOnce(new ApiError("'leonardo' is not connected.", 502));

    const store = useResultsStore();
    await store.fetchResults(CLUSTER, SYSTEM, JOB_BASENAME);

    expect(store.fetchBusy[KEY]).toBe(false);
    expect(store.fetchError[KEY]).toBe("'leonardo' is not connected.");
  });
});

describe("results store: index", () => {
  it("loadIndex populates the jobs list on success", async () => {
    const jobs = [
      {
        cluster: CLUSTER,
        system: SYSTEM,
        job_basename: JOB_BASENAME,
        connected: true,
        status: "COMPLETED",
        record_id: null,
        job_id: null,
        submitted_at: null,
        cached: true,
        cached_bytes: 4096,
        possibly_stale: false,
      },
    ];
    indexMock.mockResolvedValueOnce({ jobs });

    const store = useResultsStore();
    await store.loadIndex();

    expect(store.index).toEqual(jobs);
    expect(store.indexBusy).toBe(false);
    expect(store.indexError).toBeNull();
  });

  it("loadIndex(true) forces a reload even when already loaded, keeping stale data on error", async () => {
    const jobs = [
      {
        cluster: CLUSTER,
        system: SYSTEM,
        job_basename: JOB_BASENAME,
        connected: true,
        status: "COMPLETED",
        record_id: null,
        job_id: null,
        submitted_at: null,
        cached: true,
        cached_bytes: 4096,
        possibly_stale: false,
      },
    ];
    indexMock.mockResolvedValueOnce({ jobs });
    const store = useResultsStore();
    await store.loadIndex();

    indexMock.mockRejectedValueOnce(new ApiError("Cannot reach the dashboard backend.", 0));
    await store.loadIndex(true);

    expect(indexMock).toHaveBeenCalledTimes(2);
    expect(store.indexError).toBe("Cannot reach the dashboard backend.");
    expect(store.index).toEqual(jobs);
  });

  it("skips a second unforced call once the index is already loaded (plan 079)", async () => {
    indexMock.mockResolvedValueOnce({ jobs: [] });
    const store = useResultsStore();
    await store.loadIndex();

    await store.loadIndex();

    expect(indexMock).toHaveBeenCalledTimes(1);
  });

  it("a prior index load error still allows an unforced retry (plan 079)", async () => {
    indexMock.mockRejectedValueOnce(new ApiError("Cannot reach the dashboard backend.", 0));
    const store = useResultsStore();
    await store.loadIndex();
    expect(store.indexError).toBeDefined();

    indexMock.mockResolvedValueOnce({ jobs: [] });
    await store.loadIndex();

    expect(indexMock).toHaveBeenCalledTimes(2);
    expect(store.indexError).toBeNull();
  });
});

describe("results store: cache size + clear", () => {
  it("refreshCacheSize loads the total", async () => {
    cacheSizeMock.mockResolvedValueOnce({ total_bytes: 128 });

    const store = useResultsStore();
    await store.refreshCacheSize();

    expect(store.cacheSize).toBe(128);
  });

  it("clearCache empties loaded results and re-reads a zero size", async () => {
    getMock.mockResolvedValueOnce(SAMPLE_DATA);
    cacheSizeMock.mockResolvedValueOnce({ total_bytes: 0 });

    const store = useResultsStore();
    await store.loadResults(CLUSTER, SYSTEM, JOB_BASENAME);
    expect(store.results[KEY]).toEqual(SAMPLE_DATA);

    await store.clearCache();

    expect(clearCacheMock).toHaveBeenCalled();
    expect(store.results).toEqual({});
    expect(store.cacheSize).toBe(0);
  });
});
