import { afterEach, describe, expect, it, vi } from "vitest";

const { downloadImage } = vi.hoisted(() => ({
  downloadImage: vi.fn().mockResolvedValue("data:image/png;base64,"),
}));
vi.mock("@/lib/plotlyBundle", () => ({
  default: { downloadImage },
}));

import {
  defaultAxisPair,
  exportChartImage,
  makePlotlyLayout,
  makePlotlyTraces,
} from "@/lib/resultsPlot";

describe("makePlotlyTraces", () => {
  const rows = [
    { msg_size: 1, latency_s: 0.1 },
    { msg_size: 2, latency_s: 0.2 },
    { msg_size: null, latency_s: 0.3 },
  ];

  // No `document` exists under Vitest's node environment, so `scattergl`
  // (WebGL) support is correctly detected as unavailable here -- the same
  // safe fallback a real browser without WebGL gets. See the "WebGL
  // fallback" suite below for the scattergl-available path.
  it("builds a marker scatter trace for scatter kind, skipping null rows", () => {
    const [trace] = makePlotlyTraces(
      rows,
      "msg_size",
      "latency_s",
      "scatter",
      { div: 1, label: "" },
      "#111",
      "exp1",
    );
    expect(trace).toMatchObject({
      type: "scatter",
      mode: "markers",
      name: "exp1",
      x: [1, 2],
      y: [0.1, 0.2],
      marker: { color: "#111", size: 7 },
    });
  });

  it("sorts points by X ascending regardless of row order (owner bug report: unsorted rows made a line chart's X axis jump around)", () => {
    const shuffled = [
      { msg_size: 4, latency_s: 0.4 },
      { msg_size: 1, latency_s: 0.1 },
      { msg_size: 2, latency_s: 0.2 },
    ];
    const [trace] = makePlotlyTraces(
      shuffled,
      "msg_size",
      "latency_s",
      "line",
      { div: 1, label: "" },
      "#111",
      "exp1",
    );
    expect(trace).toMatchObject({ x: [1, 2, 4], y: [0.1, 0.2, 0.4] });
  });

  it("builds a lines-mode scatter trace for line kind", () => {
    const [trace] = makePlotlyTraces(
      rows,
      "msg_size",
      "latency_s",
      "line",
      { div: 1, label: "" },
      "#111",
      "exp1",
    );
    expect(trace).toMatchObject({ type: "scatter", mode: "lines" });
  });

  it("scales y values by the given unit", () => {
    const [trace] = makePlotlyTraces(
      rows,
      "msg_size",
      "latency_s",
      "scatter",
      { div: 1e-3, label: "ms" },
      "#111",
      "exp1",
    );
    expect((trace as { y: number[] }).y).toEqual([100, 200]);
  });

  it("builds a bar trace of per-bin averages, formatting size-shaped x as bytes", () => {
    const binned = [
      { bytes: 1024, latency_s: 0.1 },
      { bytes: 1024, latency_s: 0.3 },
      { bytes: 2048, latency_s: 0.2 },
    ];
    const [trace] = makePlotlyTraces(
      binned,
      "bytes",
      "latency_s",
      "bar",
      { div: 1, label: "" },
      "#222",
      "exp2",
    );
    expect(trace).toMatchObject({
      type: "bar",
      name: "exp2",
      x: ["1 KiB", "2 KiB"],
      y: [0.2, 0.2],
    });
  });

  it("builds a violin trace repeating x per raw point in each bin", () => {
    const binned = [
      { grp: 1, v: 10 },
      { grp: 1, v: 20 },
      { grp: 2, v: 30 },
    ];
    const [trace] = makePlotlyTraces(
      binned,
      "grp",
      "v",
      "violin",
      { div: 1, label: "" },
      "#333",
      "exp3",
    );
    expect(trace).toMatchObject({
      type: "violin",
      x: ["1", "1", "2"],
      y: [10, 20, 30],
      box: { visible: true },
    });
  });
});

describe("makePlotlyLayout", () => {
  it("uses a paper-white background and print font regardless of theme", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
    );
    expect(layout.paper_bgcolor).toBe("#ffffff");
    expect(layout.plot_bgcolor).toBe("#ffffff");
    expect((layout.font as { family: string }).family).toMatch(/serif/i);
  });

  it("maps the scale toggle to a Plotly x-axis type for scatter/line", () => {
    const linear = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
    );
    const log = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "logarithmic",
      false,
    );
    expect((linear.xaxis as { type: string }).type).toBe("linear");
    expect((log.xaxis as { type: string }).type).toBe("log");
  });

  it("forces a category x-axis for bar/violin regardless of the scale toggle", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "violin",
      "logarithmic",
      false,
    );
    expect((layout.xaxis as { type: string }).type).toBe("category");
  });

  it("appends the unit label to axis titles when present", () => {
    const layout = makePlotlyLayout(
      "latency",
      "throughput",
      { div: 1e-3, label: "ms" },
      { div: 1e3, label: "K" },
      "scatter",
      "linear",
      false,
    );
    expect((layout.xaxis as { title: { text: string } }).title.text).toBe("latency (ms)");
    expect((layout.yaxis as { title: { text: string } }).title.text).toBe("throughput (K)");
  });

  it("has no explicit y-axis range by default (each chart autoscales to its own data)", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
    );
    expect((layout.yaxis as { range?: unknown }).range).toBeUndefined();
  });

  it("pads a given y-range by 5% each side, for small multiples to share one axis", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
      [0, 100],
    );
    expect((layout.yaxis as { range: [number, number] }).range).toEqual([-5, 105]);
  });

  it("pads a zero-width y-range with a small fixed margin instead of a degenerate range", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
      [10, 10],
    );
    const [lo, hi] = (layout.yaxis as { range: [number, number] }).range;
    expect(lo).toBeLessThan(10);
    expect(hi).toBeGreaterThan(10);
  });

  it("fixes a bar/violin x-axis to a given category order (owner bug report: selection order otherwise wins)", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "bar",
      "linear",
      false,
      undefined,
      ["1 KiB", "2 KiB", "4 KiB"],
    );
    expect((layout.xaxis as { categoryorder: string }).categoryorder).toBe("array");
    expect((layout.xaxis as { categoryarray: string[] }).categoryarray).toEqual([
      "1 KiB",
      "2 KiB",
      "4 KiB",
    ]);
  });

  it("does not set a category order for scatter/line (a real numeric axis, not categorical)", () => {
    const layout = makePlotlyLayout(
      "x",
      "y",
      { div: 1, label: "" },
      { div: 1, label: "" },
      "scatter",
      "linear",
      false,
      undefined,
      ["1", "2"],
    );
    expect((layout.xaxis as { categoryorder?: string }).categoryorder).toBeUndefined();
  });
});

describe("defaultAxisPair", () => {
  it("prefers a size-shaped column as x and a time-shaped column as y", () => {
    expect(defaultAxisPair(["run_id", "msg_size", "avg_duration_s"])).toEqual({
      x: "msg_size",
      y: "avg_duration_s",
    });
  });

  it("falls back to column order when nothing is recognized", () => {
    expect(defaultAxisPair(["alpha", "beta", "gamma"])).toEqual({ x: "alpha", y: "beta" });
  });

  it("returns empty strings for no columns", () => {
    expect(defaultAxisPair([])).toEqual({ x: "", y: "" });
  });

  it("falls back to the same column for x and y when there is only one", () => {
    expect(defaultAxisPair(["only_col"])).toEqual({ x: "only_col", y: "only_col" });
  });
});

describe("exportChartImage", () => {
  it("wraps Plotly.downloadImage with print-quality fixed dimensions", async () => {
    const gd = {} as HTMLElement;
    await exportChartImage(gd, "png", "my-chart");
    expect(downloadImage).toHaveBeenCalledWith(gd, {
      format: "png",
      width: 1600,
      height: 900,
      filename: "my-chart",
    });
  });
});

describe("WebGL fallback (owner: max compatibility everywhere)", () => {
  // `hasWebglSupport` memoizes at module scope, so each case needs a fresh
  // module instance (vi.resetModules + a dynamic re-import) to see its own
  // fake `document`, not a state left over from an earlier case.
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports unsupported outside a browser (no document) -- the real Vitest environment", async () => {
    vi.resetModules();
    const { hasWebglSupport } = await import("@/lib/resultsPlot");
    expect(hasWebglSupport()).toBe(false);
  });

  it("uses scattergl when the browser can actually create a WebGL context", async () => {
    vi.stubGlobal("document", {
      createElement: () => ({ getContext: (kind: string) => (kind === "webgl" ? {} : null) }),
    });
    vi.resetModules();
    const { makePlotlyTraces } = await import("@/lib/resultsPlot");
    const [trace] = makePlotlyTraces(
      [{ x: 1, y: 1 }],
      "x",
      "y",
      "scatter",
      { div: 1, label: "" },
      "#111",
      "s",
    );
    expect(trace).toMatchObject({ type: "scattergl" });
  });

  it("falls back to plain scatter when WebGL context creation fails (e.g. no GPU/driver support)", async () => {
    vi.stubGlobal("document", { createElement: () => ({ getContext: () => null }) });
    vi.resetModules();
    const { makePlotlyTraces } = await import("@/lib/resultsPlot");
    const [trace] = makePlotlyTraces(
      [{ x: 1, y: 1 }],
      "x",
      "y",
      "scatter",
      { div: 1, label: "" },
      "#111",
      "s",
    );
    expect(trace).toMatchObject({ type: "scatter" });
  });
});
