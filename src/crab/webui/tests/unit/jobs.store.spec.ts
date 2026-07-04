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

beforeEach(() => {
  setActivePinia(createPinia());
  listMock.mockReset();
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
