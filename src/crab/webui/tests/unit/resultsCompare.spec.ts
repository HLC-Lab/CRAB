import { describe, expect, it } from "vitest";
import { makeOverlayChartData, resolveCol } from "@/lib/resultsCompare";

describe("resolveCol", () => {
  it("returns the column unchanged when it already exists", () => {
    expect(resolveCol([{ duration: 1 }], "duration")).toBe("duration");
  });

  it("strips a numeric prefix and finds the matching column", () => {
    expect(resolveCol([{ "2_Avg-Duration_s": 1 }], "1_Avg-Duration_s")).toBe("2_Avg-Duration_s");
  });

  it("falls back to the original name when nothing matches", () => {
    expect(resolveCol([{ other: 1 }], "1_Avg-Duration_s")).toBe("1_Avg-Duration_s");
  });

  it("falls back on an empty row set", () => {
    expect(resolveCol([], "duration")).toBe("duration");
  });
});

describe("makeOverlayChartData", () => {
  const unit = { div: 1, label: "s" };

  it("scatter: one dataset per experiment, sharing the same axes", () => {
    const spec = makeOverlayChartData(
      [
        { name: "A", color: "#111", rows: [{ x: 1, y: 2 }] },
        { name: "B", color: "#222", rows: [{ x: 1, y: 4 }] },
      ],
      "x",
      "y",
      "scatter",
      unit,
    );
    expect(spec.labels).toBeUndefined();
    expect(spec.datasets).toHaveLength(2);
    expect(spec.datasets.map((d) => d.label)).toEqual(["A", "B"]);
  });

  it("scatter: resolves per-experiment column prefixes onto the shared axis names", () => {
    const spec = makeOverlayChartData(
      [
        { name: "A", color: "#111", rows: [{ "1_x": 1, "1_y": 2 }] },
        { name: "B", color: "#222", rows: [{ "2_x": 1, "2_y": 5 }] },
      ],
      "1_x",
      "1_y",
      "scatter",
      unit,
    );
    expect(spec.datasets[0].data).toEqual([{ x: 1, y: 2 }]);
    expect(spec.datasets[1].data).toEqual([{ x: 1, y: 5 }]);
  });

  it("bar: merges bins across experiments into a shared label set", () => {
    const spec = makeOverlayChartData(
      [
        {
          name: "A",
          color: "#111",
          rows: [
            { nodes: 1, duration: 2 },
            { nodes: 2, duration: 4 },
          ],
        },
        { name: "B", color: "#222", rows: [{ nodes: 1, duration: 6 }] },
      ],
      "nodes",
      "duration",
      "bar",
      unit,
    );
    expect(spec.labels).toEqual(["1", "2"]);
    expect(spec.datasets).toHaveLength(2);
    expect(spec.datasets[0].data).toEqual([2, 4]);
    expect(spec.datasets[1].data).toEqual([6, null]);
  });

  it("violin: merges bins into per-experiment value arrays", () => {
    const spec = makeOverlayChartData(
      [{ name: "A", color: "#111", rows: [{ nodes: 1, duration: 2 }] }],
      "nodes",
      "duration",
      "violin",
      unit,
    );
    expect(spec.datasets[0].data).toEqual([[2]]);
  });
});
