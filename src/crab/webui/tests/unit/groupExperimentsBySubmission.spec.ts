import { describe, expect, it } from "vitest";
import { groupExperimentsBySubmission } from "@/lib/groupExperimentsBySubmission";

function row(overrides: Record<string, unknown> = {}) {
  return {
    cluster: "leonardo",
    system: "leonardo",
    job_name: "demo",
    experiment_name: "01_baseline",
    timestamp: "2026-07-04_20-03-37",
    numnodes: "4",
    ppn: "2",
    apps_list: "netgauge",
    status: "COMPLETED",
    tags: "",
    relative_path: "./demo_2026-07-04_20-03-37/01_baseline",
    record_id: "leonardo:1",
    job_id: "1",
    submitted_at: "2026-07-04T20:03:37Z",
    ...overrides,
  };
}

describe("groupExperimentsBySubmission", () => {
  it("groups rows with the same record_id into one submission", () => {
    const groups = groupExperimentsBySubmission([
      row({ experiment_name: "01_baseline" }),
      row({ experiment_name: "02_variant" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].experiments.map((e) => e.experiment_name)).toEqual([
      "01_baseline",
      "02_variant",
    ]);
  });

  it("groups rows with no record_id by their data_dir basename", () => {
    const groups = groupExperimentsBySubmission([
      row({
        record_id: null,
        submitted_at: null,
        relative_path: "./manual_run_2026-01-01/01_x",
      }),
      row({
        record_id: null,
        submitted_at: null,
        relative_path: "./manual_run_2026-01-01/02_y",
      }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].recordId).toBeNull();
  });

  it("sorts groups newest-first by submitted_at", () => {
    const groups = groupExperimentsBySubmission([
      row({ record_id: "leonardo:1", submitted_at: "2026-01-01T00:00:00Z" }),
      row({ record_id: "leonardo:2", submitted_at: "2026-02-01T00:00:00Z" }),
    ]);
    expect(groups.map((g) => g.recordId)).toEqual(["leonardo:2", "leonardo:1"]);
  });

  it("returns one group unchanged for a single-submission history", () => {
    const groups = groupExperimentsBySubmission([row(), row({ experiment_name: "02_variant" })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].experiments).toHaveLength(2);
  });
});
