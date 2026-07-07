import { describe, expect, it, vi } from "vitest";

// resultsCompare.ts pulls in resultsPlot.ts, whose real Plotly bundle assumes
// a browser global (`self`) that doesn't exist under vitest's node
// environment -- mocked at the module boundary, same as resultsPlot.spec.ts.
vi.mock("plotly.js-cartesian-dist-min", () => ({ default: {} }));

import {
  makeOverlayTraces,
  makeSmallMultipleTraces,
  resolveCol,
  sharedColumns,
  sharedUnit,
  type CompareSeries,
} from "@/lib/resultsCompare";

describe("resolveCol", () => {
  it("returns the column unchanged when it already exists", () => {
    expect(resolveCol([{ msg_size: 1 }], "msg_size")).toBe("msg_size");
  });

  it("finds a numeric-prefixed match for the same suffix", () => {
    expect(resolveCol([{ "2_Avg-Duration_s": 0.5 }], "1_Avg-Duration_s")).toBe("2_Avg-Duration_s");
  });

  it("falls back to the original column when nothing matches", () => {
    expect(resolveCol([{ other: 1 }], "1_Avg-Duration_s")).toBe("1_Avg-Duration_s");
  });

  it("falls back when rows are empty", () => {
    expect(resolveCol([], "msg_size")).toBe("msg_size");
  });
});

describe("sharedColumns", () => {
  it("returns only columns present in every series, not a union (S17 regression)", () => {
    // Series A only has msg_size; series B only has n. A union would let a
    // caller pick msg_size as an axis and silently render series B with no
    // data at all -- the intersection must exclude both msg_size and n.
    const seriesA: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ msg_size: 1024, avg_duration_s: 0.5 }],
    };
    const seriesB: CompareSeries = {
      id: "b",
      label: "B",
      color: "#222",
      rows: [{ n: 1, avg_duration_s: 5e-8 }],
    };

    expect(sharedColumns([seriesA, seriesB])).toEqual(["avg_duration_s"]);
  });

  it("matches numeric-prefixed columns by their canonical suffix", () => {
    const seriesA: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ "1_bw": 5000, "1_lat": 1 }],
    };
    const seriesB: CompareSeries = {
      id: "b",
      label: "B",
      color: "#222",
      rows: [{ "2_bw": 9000 }],
    };

    expect(sharedColumns([seriesA, seriesB])).toEqual(["bw"]);
  });

  it("returns an empty list when nothing is selected", () => {
    expect(sharedColumns([])).toEqual([]);
  });

  it("returns an empty list when series share no numeric column", () => {
    const seriesA: CompareSeries = { id: "a", label: "A", color: "#111", rows: [{ x: 1 }] };
    const seriesB: CompareSeries = { id: "b", label: "B", color: "#222", rows: [{ y: 2 }] };
    expect(sharedColumns([seriesA, seriesB])).toEqual([]);
  });
});

describe("sharedUnit", () => {
  it("computes one unit across every series, not per series (decision 11 regression)", () => {
    // Series A alone (values ~0.5) would pick 'ms'; series B alone (~5e-8)
    // would pick 'ns' -- combined, the shared max (0.5) must win for BOTH, so
    // a caller can never render two cards with different units for "the same" axis.
    const seriesA: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ duration: 0.5 }],
    };
    const seriesB: CompareSeries = {
      id: "b",
      label: "B",
      color: "#222",
      rows: [{ duration: 5e-8 }],
    };

    const unit = sharedUnit([seriesA, seriesB], "duration");

    expect(unit).toEqual({ div: 1e-3, label: "ms" });
  });

  it("resolves numeric-prefixed columns per series before combining values", () => {
    const seriesA: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ "1_throughput": 5000 }],
    };
    const seriesB: CompareSeries = {
      id: "b",
      label: "B",
      color: "#222",
      rows: [{ "2_throughput": 9000 }],
    };

    expect(sharedUnit([seriesA, seriesB], "1_throughput")).toEqual({ div: 1e3, label: "K" });
  });

  it("ignores non-numeric values", () => {
    const series: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ count: "n/a" }, { count: 2 }],
    };
    expect(sharedUnit([series], "count")).toEqual({ div: 1, label: "" });
  });
});

describe("makeOverlayTraces", () => {
  const seriesA: CompareSeries = {
    id: "a",
    label: "A",
    color: "#111",
    rows: [{ x: 1, y: 100 }],
  };
  const seriesB: CompareSeries = {
    id: "b",
    label: "B",
    color: "#222",
    rows: [{ x: 2, y: 200 }],
  };

  it("builds one trace per series sharing the same axes", () => {
    const traces = makeOverlayTraces([seriesA, seriesB], "x", "y", "scatter", {
      div: 1,
      label: "",
    });
    expect(traces).toHaveLength(2);
    expect(traces[0]).toMatchObject({ name: "A", x: [1], y: [100] });
    expect(traces[1]).toMatchObject({ name: "B", x: [2], y: [200] });
  });

  it("scales every trace by the same shared unit", () => {
    const traces = makeOverlayTraces([seriesA, seriesB], "x", "y", "scatter", {
      div: 10,
      label: "d10",
    });
    expect((traces[0] as unknown as { y: number[] }).y).toEqual([10]);
    expect((traces[1] as unknown as { y: number[] }).y).toEqual([20]);
  });

  it("remaps numeric-prefixed columns per series before plotting", () => {
    const prefixedA: CompareSeries = {
      id: "a",
      label: "A",
      color: "#111",
      rows: [{ "1_x": 1, "1_y": 100 }],
    };
    const prefixedB: CompareSeries = {
      id: "b",
      label: "B",
      color: "#222",
      rows: [{ "2_x": 2, "2_y": 200 }],
    };
    const traces = makeOverlayTraces([prefixedA, prefixedB], "1_x", "1_y", "scatter", {
      div: 1,
      label: "",
    });
    expect(traces[0]).toMatchObject({ x: [1], y: [100] });
    expect(traces[1]).toMatchObject({ x: [2], y: [200] });
  });
});

describe("makeSmallMultipleTraces", () => {
  const seriesA: CompareSeries = {
    id: "a",
    label: "A",
    color: "#111",
    rows: [{ x: 1, y: 0.5 }],
  };
  const seriesB: CompareSeries = {
    id: "b",
    label: "B",
    color: "#222",
    rows: [{ x: 2, y: 5e-8 }],
  };

  it("builds one independent trace set per series, id and label preserved", () => {
    const cards = makeSmallMultipleTraces([seriesA, seriesB], "x", "y", "scatter", {
      div: 1,
      label: "s",
    });
    expect(cards.map((c) => c.id)).toEqual(["a", "b"]);
    expect(cards.map((c) => c.label)).toEqual(["A", "B"]);
  });

  it("applies the SAME passed-in unit to every card (decision 11 regression)", () => {
    const unit = sharedUnit([seriesA, seriesB], "y");
    const cards = makeSmallMultipleTraces([seriesA, seriesB], "x", "y", "scatter", unit);

    // Both cards' y values are divided by the one shared unit -- neither card
    // silently rescales itself to its own local magnitude.
    expect((cards[0].traces[0] as unknown as { y: number[] }).y).toEqual([0.5 / unit.div]);
    expect((cards[1].traces[0] as unknown as { y: number[] }).y).toEqual([5e-8 / unit.div]);
  });
});
