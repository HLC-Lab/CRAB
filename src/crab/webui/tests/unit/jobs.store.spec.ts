/**
 * Poll-coordinator behavior for the jobs store (plan 050 design: a single
 * frontend timer, in-flight guard against overlapping ticks). Mocks the API
 * client (the true I/O boundary — same principle as the backend's fake
 * Transport), not store internals.
 */
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { jobs: { list: vi.fn(), submit: vi.fn(), cancel: vi.fn(), logs: vi.fn() } },
  ApiError: class ApiError extends Error {
    detail?: string;
  },
}));

import { ApiError, api } from "@/api/client";
import { useJobsStore } from "@/stores/jobs";

const listMock = vi.mocked(api.jobs.list);
const submitMock = vi.mocked(api.jobs.submit);
const logsMock = vi.mocked(api.jobs.logs);

const SAMPLE_LOGS = {
  schema: 1,
  data_dir: "/d",
  stdout: { path: "/d/slurm_output.log", exists: true, content: "out", truncated: false },
  stderr: { path: "/d/slurm_error.log", exists: false, content: "", truncated: false },
};

beforeEach(() => {
  setActivePinia(createPinia());
  listMock.mockReset();
  logsMock.mockReset();
  submitMock.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("jobs store polling", () => {
  it("skips an overlapping tick while a refresh is still in flight", async () => {
    let resolveFirst: (v: unknown[]) => void = () => {};
    listMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    listMock.mockResolvedValue([]);

    const store = useJobsStore();
    store.startPolling();

    // First tick fires and is still pending when the second interval elapses.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(listMock).toHaveBeenCalledTimes(1); // second tick skipped: still in flight

    resolveFirst([]);
    await vi.advanceTimersByTimeAsync(0); // let the first call's .finally() run

    await vi.advanceTimersByTimeAsync(10_000);
    expect(listMock).toHaveBeenCalledTimes(2); // now free to poll again

    store.stopPolling();
  });

  it("stopPolling clears the timer so no further ticks fire", async () => {
    listMock.mockResolvedValue([]);
    const store = useJobsStore();

    store.startPolling();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listMock).toHaveBeenCalledTimes(1);

    store.stopPolling();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listMock).toHaveBeenCalledTimes(1); // no more ticks after stopping
    expect(store.polling).toBe(false);
  });

  it("startPolling is idempotent (no double interval)", async () => {
    listMock.mockResolvedValue([]);
    const store = useJobsStore();

    store.startPolling();
    store.startPolling();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listMock).toHaveBeenCalledTimes(1);

    store.stopPolling();
  });
});

describe("jobs store refresh metadata", () => {
  it("records lastRefreshedAt after a successful refresh, not before", async () => {
    listMock.mockResolvedValue([]);
    const store = useJobsStore();
    expect(store.lastRefreshedAt).toBeNull();

    await store.refresh();
    expect(store.lastRefreshedAt).toBe(Date.now());
  });

  it("leaves lastRefreshedAt untouched when a refresh fails", async () => {
    listMock.mockResolvedValueOnce([]);
    const store = useJobsStore();
    await store.refresh();
    const first = store.lastRefreshedAt;

    listMock.mockRejectedValueOnce(new ApiError("boom"));
    await vi.advanceTimersByTimeAsync(1_000);
    await store.refresh();
    expect(store.lastRefreshedAt).toBe(first);
  });
});

describe("jobs store open logs stay live", () => {
  it("openLogs fetches immediately and records which job is open", async () => {
    logsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useJobsStore();

    await store.openLogs("job-1");
    expect(logsMock).toHaveBeenCalledTimes(1);
    expect(store.openLogId).toBe("job-1");
  });

  it("refresh() also refetches the currently open job's logs", async () => {
    listMock.mockResolvedValue([]);
    logsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useJobsStore();
    await store.openLogs("job-1");

    await store.refresh();
    expect(logsMock).toHaveBeenCalledTimes(2);
    expect(logsMock).toHaveBeenLastCalledWith("job-1");
  });

  it("auto-poll keeps an open log panel updated, not just the job list", async () => {
    listMock.mockResolvedValue([]);
    logsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useJobsStore();
    await store.openLogs("job-1");

    store.startPolling();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(logsMock).toHaveBeenCalledTimes(2);

    store.stopPolling();
  });

  it("closeLogs stops refresh() from refetching it", async () => {
    listMock.mockResolvedValue([]);
    logsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useJobsStore();
    await store.openLogs("job-1");

    store.closeLogs();
    await store.refresh();
    expect(logsMock).toHaveBeenCalledTimes(1); // no second call after closing
  });

  it("does not flip logsBusy on a refetch once content already exists (avoids the flicker)", async () => {
    listMock.mockResolvedValue([]);
    logsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useJobsStore();
    await store.openLogs("job-1");
    expect(store.logsBusy["job-1"]).toBe(false);

    let resolveSecond: (v: typeof SAMPLE_LOGS) => void = () => {};
    logsMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
    );
    const refreshPromise = store.refresh();
    await Promise.resolve(); // let refresh() reach the in-flight logs fetch
    expect(store.logsBusy["job-1"]).toBe(false); // stays false: content already exists

    resolveSecond(SAMPLE_LOGS);
    await refreshPromise;
  });
});

describe("jobs store poll interval", () => {
  it("defaults to 10s", () => {
    const store = useJobsStore();
    expect(store.pollIntervalMs).toBe(10_000);
  });

  it("setPollInterval takes effect immediately while already polling", async () => {
    listMock.mockResolvedValue([]);
    const store = useJobsStore();
    store.startPolling();

    store.setPollInterval(5_000);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(listMock).toHaveBeenCalledTimes(1);

    store.stopPolling();
  });

  it("setPollInterval before polling starts just updates the stored value", () => {
    const store = useJobsStore();
    store.setPollInterval(30_000);
    expect(store.pollIntervalMs).toBe(30_000);
  });
});

describe("jobs store error surfacing", () => {
  it("submit includes the remote detail (e.g. stderr) alongside the message", async () => {
    const err = new ApiError("");
    Object.assign(err, {
      message: "`crab run staged.json -p leonardo --json` failed on the cluster (exit 1).",
      detail: "ModuleNotFoundError: No module named 'crab'",
    });
    submitMock.mockRejectedValueOnce(err);

    const store = useJobsStore();
    const result = await store.submit({ profile_name: "leonardo", config_id: "cfg" });

    expect(result).toBeNull();
    expect(store.submitError).toContain("failed on the cluster");
    expect(store.submitError).toContain("ModuleNotFoundError");
  });
});
