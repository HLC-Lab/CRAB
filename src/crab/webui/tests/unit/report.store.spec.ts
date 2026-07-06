/**
 * Per-use-case experiment report store (plan 060): fetches
 * GET /api/jobs/report/{config_name} and lazily loads per-app error logs for
 * one experiment at a time. Mocks the API client, same principle as
 * jobs.store.spec.ts.
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { jobs: { report: vi.fn(), experimentLogs: vi.fn() } },
  ApiError: class ApiError extends Error {
    detail?: string;
  },
}));

import { ApiError, api } from "@/api/client";
import { useReportStore } from "@/stores/report";

const reportMock = vi.mocked(api.jobs.report);
const experimentLogsMock = vi.mocked(api.jobs.experimentLogs);

const SAMPLE_REPORT = {
  config_name: "msgsize_scaling_study",
  clusters_skipped: [],
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
};

const SAMPLE_LOGS = {
  schema: 1,
  data_dir: "/d/01_baseline",
  files: [
    {
      app_id: "0",
      path: "/d/01_baseline/error_app_0.log",
      exists: true,
      content: "boom",
      truncated: false,
    },
  ],
};

beforeEach(() => {
  setActivePinia(createPinia());
  reportMock.mockReset();
  experimentLogsMock.mockReset();
});

describe("report store fetch", () => {
  it("loads a report by config name", async () => {
    reportMock.mockResolvedValue(SAMPLE_REPORT);
    const store = useReportStore();

    await store.fetchReport("msgsize_scaling_study");
    expect(reportMock).toHaveBeenCalledWith("msgsize_scaling_study");
    expect(store.report).toEqual(SAMPLE_REPORT);
    expect(store.error).toBeNull();
  });

  it("surfaces an API error and leaves report null", async () => {
    const err = new ApiError("");
    Object.assign(err, { message: "boom" });
    reportMock.mockRejectedValueOnce(err);
    const store = useReportStore();

    await store.fetchReport("nope");
    expect(store.report).toBeNull();
    expect(store.error).toBe("boom");
  });

  it("sets loading only while the fetch is in flight", async () => {
    let resolveFetch: (v: typeof SAMPLE_REPORT) => void = () => {};
    reportMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const store = useReportStore();

    const p = store.fetchReport("msgsize_scaling_study");
    expect(store.loading).toBe(true);
    resolveFetch(SAMPLE_REPORT);
    await p;
    expect(store.loading).toBe(false);
  });
});

describe("report store per-experiment logs", () => {
  it("toggleExperimentLogs opens and lazily fetches logs for one experiment", async () => {
    experimentLogsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useReportStore();

    await store.toggleExperimentLogs("leonardo:1", "01_baseline");
    expect(experimentLogsMock).toHaveBeenCalledWith("leonardo:1", "01_baseline");
    expect(store.openExperimentKey).toBe("leonardo:1/01_baseline");
    expect(store.experimentLogs["leonardo:1/01_baseline"]).toEqual(SAMPLE_LOGS);
  });

  it("toggling the same experiment again closes it without refetching", async () => {
    experimentLogsMock.mockResolvedValue(SAMPLE_LOGS);
    const store = useReportStore();

    await store.toggleExperimentLogs("leonardo:1", "01_baseline");
    await store.toggleExperimentLogs("leonardo:1", "01_baseline");

    expect(store.openExperimentKey).toBeNull();
    expect(experimentLogsMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a per-experiment logs error without clobbering other state", async () => {
    const err = new ApiError("");
    Object.assign(err, { message: "no experiment directory" });
    experimentLogsMock.mockRejectedValueOnce(err);
    const store = useReportStore();

    await store.toggleExperimentLogs("leonardo:1", "ghost");
    expect(store.experimentLogsError["leonardo:1/ghost"]).toBe("no experiment directory");
  });
});

describe("report store per-experiment rerun selection", () => {
  it("toggleSelected adds then removes a (record, experiment) pair", () => {
    const store = useReportStore();
    store.toggleSelected("leonardo:1", "01_baseline");
    expect(store.selected.has("leonardo:1/01_baseline")).toBe(true);

    store.toggleSelected("leonardo:1", "01_baseline");
    expect(store.selected.has("leonardo:1/01_baseline")).toBe(false);
  });

  it("selectedRecordIds reflects the distinct job records among selected rows", () => {
    const store = useReportStore();
    store.toggleSelected("leonardo:1", "01_baseline");
    store.toggleSelected("leonardo:1", "02_variant");
    expect(store.selectedRecordIds).toEqual(new Set(["leonardo:1"]));

    store.toggleSelected("leonardo:2", "03_other_job");
    expect(store.selectedRecordIds).toEqual(new Set(["leonardo:1", "leonardo:2"]));
  });

  it("clearSelected empties the selection", () => {
    const store = useReportStore();
    store.toggleSelected("leonardo:1", "01_baseline");
    store.clearSelected();
    expect(store.selected.size).toBe(0);
  });
});

describe("report store selection mode (plan 076)", () => {
  it("starts off, and toggling turns it on", () => {
    const store = useReportStore();
    expect(store.selectionMode).toBe(false);
    store.toggleSelectionMode();
    expect(store.selectionMode).toBe(true);
  });

  it("toggling off clears any selection made while it was on", () => {
    const store = useReportStore();
    store.toggleSelectionMode();
    store.toggleSelected("leonardo:1", "01_baseline");

    store.toggleSelectionMode();
    expect(store.selectionMode).toBe(false);
    expect(store.selected.size).toBe(0);
  });

  it("exitSelectionMode turns it off and clears selection directly", () => {
    const store = useReportStore();
    store.toggleSelectionMode();
    store.toggleSelected("leonardo:1", "01_baseline");

    store.exitSelectionMode();
    expect(store.selectionMode).toBe(false);
    expect(store.selected.size).toBe(0);
  });
});
