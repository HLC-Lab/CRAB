/**
 * Progress line: the text helpers and the polling store, with the API client mocked.
 * Fixture literals mirror what GET /api/remotes/{name}/progress returns.
 */
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { remotes: { progress: vi.fn() } },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/api/client";
import type { ProgressStage } from "@/api/types";
import { elapsedText, limitText, stageText } from "@/lib/progress";
import { useProgressStore } from "@/stores/progress";

const CONNECT: ProgressStage = {
  stage: "ssh-connect",
  label: "Connecting to the cluster",
  waiting_for:
    "the login node (login.example.org:22) to accept the connection and your credentials",
  started_at: 1000,
  timeout_s: 30,
};

describe("progress text", () => {
  it("counts whole seconds, never negative", () => {
    expect(elapsedText(1000, 1012.4)).toBe("12 s");
    expect(elapsedText(1000, 1000)).toBe("0 s");
    expect(elapsedText(1000, 990)).toBe("0 s");
  });

  it("switches to minutes after a minute", () => {
    expect(elapsedText(1000, 1065)).toBe("1 min 5 s");
    expect(elapsedText(1000, 1120)).toBe("2 min");
  });

  it("says when the step gives up", () => {
    expect(limitText(30)).toBe("gives up after 30 s");
    expect(limitText(600)).toBe("gives up after 10 min");
    expect(limitText(90)).toBe("gives up after 90 s");
    expect(limitText(null)).toBe("");
    expect(limitText(undefined)).toBe("");
  });

  it("names what is happening and what it waits for", () => {
    expect(stageText(CONNECT)).toBe(
      "Connecting to the cluster: waiting for the login node (login.example.org:22) to accept the connection and your credentials",
    );
  });

  it("uses no long dashes", () => {
    const all = [elapsedText(0, 4000), limitText(600), stageText(CONNECT)].join(" ");
    expect(all).not.toMatch(/[–—]/);
  });
});

describe("progress store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.mocked(api.remotes.progress).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls at once, then every second, and keeps the last answer", async () => {
    vi.mocked(api.remotes.progress).mockResolvedValue({ now: 1005, stages: [CONNECT] });
    const store = useProgressStore();
    store.watch("leo");
    await vi.advanceTimersByTimeAsync(0);
    expect(api.remotes.progress).toHaveBeenCalledTimes(1);
    expect(api.remotes.progress).toHaveBeenCalledWith("leo");
    expect(store.current.leo).toEqual({ now: 1005, stages: [CONNECT] });
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.remotes.progress).toHaveBeenCalledTimes(4);
    store.stop("leo");
  });

  it("stops polling and forgets the stage on stop", async () => {
    vi.mocked(api.remotes.progress).mockResolvedValue({ now: 1005, stages: [CONNECT] });
    const store = useProgressStore();
    store.watch("leo");
    await vi.advanceTimersByTimeAsync(1000);
    store.stop("leo");
    const calls = vi.mocked(api.remotes.progress).mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.remotes.progress).toHaveBeenCalledTimes(calls);
    expect(store.current.leo).toBeUndefined();
  });

  it("keeps polling until every watcher of a cluster has stopped", async () => {
    vi.mocked(api.remotes.progress).mockResolvedValue({ now: 1, stages: [] });
    const store = useProgressStore();
    store.watch("leo");
    store.watch("leo");
    store.stop("leo");
    await vi.advanceTimersByTimeAsync(2000);
    expect(vi.mocked(api.remotes.progress).mock.calls.length).toBeGreaterThan(1);
    store.stop("leo");
    const calls = vi.mocked(api.remotes.progress).mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.remotes.progress).toHaveBeenCalledTimes(calls);
  });

  it("ignores a failed poll and keeps the last stage", async () => {
    vi.mocked(api.remotes.progress)
      .mockResolvedValueOnce({ now: 1005, stages: [CONNECT] })
      .mockRejectedValue(new Error("offline"));
    const store = useProgressStore();
    store.watch("leo");
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.current.leo?.stages).toEqual([CONNECT]);
    store.stop("leo");
  });

  it("never runs two polls of one cluster at once", async () => {
    let release: (v: { now: number; stages: ProgressStage[] }) => void = () => {};
    vi.mocked(api.remotes.progress).mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    );
    const store = useProgressStore();
    store.watch("leo");
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.remotes.progress).toHaveBeenCalledTimes(1);
    release({ now: 1, stages: [] });
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.remotes.progress).toHaveBeenCalledTimes(2);
    store.stop("leo");
  });

  it("drops an answer that arrives after stop", async () => {
    let release: (v: { now: number; stages: ProgressStage[] }) => void = () => {};
    vi.mocked(api.remotes.progress).mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    );
    const store = useProgressStore();
    store.watch("leo");
    await vi.advanceTimersByTimeAsync(0);
    store.stop("leo");
    release({ now: 1005, stages: [CONNECT] });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.current.leo).toBeUndefined();
  });
});
