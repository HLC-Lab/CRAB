/**
 * Per-job detail store (plan 075): fetches GET /api/jobs/{id}/experiments.
 * Mocks the API client, same principle as report.store.spec.ts.
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { jobs: { experiments: vi.fn() } },
  ApiError: class ApiError extends Error {
    detail?: string;
  },
}));

import { ApiError, api } from "@/api/client";
import { useJobDetailStore } from "@/stores/jobDetail";

const experimentsMock = vi.mocked(api.jobs.experiments);

function detailFixture(overrides: Record<string, unknown> = {}) {
  return {
    record_id: "leonardo:1",
    config_name: "msgsize_scaling_study",
    cluster: "leonardo",
    system: "leonardo",
    job_id: "1",
    submitted_at: "2026-07-04T20:03:37Z",
    experiments: [
      {
        cluster: "leonardo",
        system: "leonardo",
        job_name: "msgsize_scaling_study",
        experiment_name: "01_baseline",
        timestamp: "2026-07-04_20-03-37",
        numnodes: "4",
        ppn: "2",
        apps_list: "netgauge",
        status: "FAILED",
        tags: "",
        relative_path: "./d/01_baseline",
        record_id: "leonardo:1",
        job_id: "1",
        submitted_at: "2026-07-04T20:03:37Z",
      },
    ],
    stale: false,
    cached_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  experimentsMock.mockReset();
});

describe("job detail store fetch", () => {
  it("loads a job's detail by record id", async () => {
    const fixture = detailFixture();
    experimentsMock.mockResolvedValue(fixture);
    const store = useJobDetailStore();

    await store.fetchDetail("leonardo:1");
    expect(experimentsMock).toHaveBeenCalledWith("leonardo:1");
    expect(store.detail).toEqual(fixture);
    expect(store.error).toBeNull();
  });

  it("surfaces an API error and leaves detail null", async () => {
    const err = new ApiError("");
    Object.assign(err, { message: "boom" });
    experimentsMock.mockRejectedValueOnce(err);
    const store = useJobDetailStore();

    await store.fetchDetail("nope");
    expect(store.detail).toBeNull();
    expect(store.error).toBe("boom");
  });

  it("sets loading only while the fetch is in flight", async () => {
    let resolveFetch: (v: ReturnType<typeof detailFixture>) => void = () => {};
    experimentsMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const store = useJobDetailStore();

    const p = store.fetchDetail("leonardo:1");
    expect(store.loading).toBe(true);
    resolveFetch(detailFixture());
    await p;
    expect(store.loading).toBe(false);
  });

  it("holds an empty experiments list without erroring", async () => {
    experimentsMock.mockResolvedValue(detailFixture({ experiments: [] }));
    const store = useJobDetailStore();

    await store.fetchDetail("leonardo:1");
    expect(store.detail?.experiments).toEqual([]);
    expect(store.error).toBeNull();
  });

  it("passes through a stale cached response with its cached_at timestamp", async () => {
    experimentsMock.mockResolvedValue(
      detailFixture({ stale: true, cached_at: "2026-07-06T10:00:00Z" }),
    );
    const store = useJobDetailStore();

    await store.fetchDetail("leonardo:1");
    expect(store.detail?.stale).toBe(true);
    expect(store.detail?.cached_at).toBe("2026-07-06T10:00:00Z");
  });

  it("passes through multiple experiments with mixed statuses unchanged", async () => {
    const mixed = detailFixture({
      experiments: [
        { ...detailFixture().experiments[0], experiment_name: "01_baseline", status: "FAILED" },
        { ...detailFixture().experiments[0], experiment_name: "02_variant", status: "COMPLETED" },
      ],
    });
    experimentsMock.mockResolvedValue(mixed);
    const store = useJobDetailStore();

    await store.fetchDetail("leonardo:1");
    expect(store.detail?.experiments.map((e) => e.status)).toEqual(["FAILED", "COMPLETED"]);
  });
});
