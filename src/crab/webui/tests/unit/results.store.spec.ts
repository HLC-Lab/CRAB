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
      fetch: vi.fn(),
      fetchStatus: vi.fn(),
      get: vi.fn(),
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

const fetchMock = vi.mocked(api.results.fetch);
const fetchStatusMock = vi.mocked(api.results.fetchStatus);
const getMock = vi.mocked(api.results.get);
const cacheSizeMock = vi.mocked(api.results.cacheSize);
const clearCacheMock = vi.mocked(api.results.clearCache);

const CLUSTER = "leonardo";
const SYSTEM = "leonardo";
const JOB_BASENAME = "demo_job";
const KEY = `${CLUSTER}/${SYSTEM}/${JOB_BASENAME}`;

const SAMPLE_DATA = { experiments: { Root: { "App 0": [{ x: 1 }] } } };

beforeEach(() => {
  setActivePinia(createPinia());
  fetchMock.mockReset();
  fetchStatusMock.mockReset();
  getMock.mockReset();
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
