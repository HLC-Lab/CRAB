import { describe, expect, it } from "vitest";
import {
  assignColors,
  autoUnit,
  autoUnitGeneric,
  binByX,
  formatBytes,
  formatVal,
  isSizeCol,
  isTimeCol,
  numericCols,
  unitForCol,
} from "@/lib/resultsChart";

describe("isSizeCol / isTimeCol", () => {
  it("recognizes known size columns case-insensitively", () => {
    expect(isSizeCol("msg_size")).toBe(true);
    expect(isSizeCol("BYTES")).toBe(true);
    expect(isSizeCol("duration")).toBe(false);
  });

  it("recognizes time-shaped columns by pattern", () => {
    expect(isTimeCol("avg_duration_s")).toBe(true);
    expect(isTimeCol("latency")).toBe(true);
    expect(isTimeCol("bandwidth_gbps")).toBe(true);
    expect(isTimeCol("msg_size")).toBe(false);
  });
});

describe("autoUnit / autoUnitGeneric", () => {
  it("picks the largest time unit that keeps values >= 1", () => {
    expect(autoUnit([0.5, 2])).toEqual({ div: 1, label: "s" });
    expect(autoUnit([0.5e-3])).toEqual({ div: 1e-6, label: "μs" });
    expect(autoUnit([5e-8])).toEqual({ div: 1e-9, label: "ns" });
  });

  it("falls back to raw when nothing is finite/positive", () => {
    expect(autoUnit([])).toEqual({ div: 1, label: "" });
    expect(autoUnit([-1, NaN])).toEqual({ div: 1, label: "" });
  });

  it("picks a metric prefix for generic magnitudes", () => {
    expect(autoUnitGeneric([500])).toEqual({ div: 1, label: "" });
    expect(autoUnitGeneric([5000])).toEqual({ div: 1e3, label: "K" });
    expect(autoUnitGeneric([5e9])).toEqual({ div: 1e9, label: "G" });
  });

  it("unitForCol dispatches on column shape", () => {
    expect(unitForCol("avg_duration_s", [0.002])).toEqual({ div: 1e-3, label: "ms" });
    expect(unitForCol("throughput", [5000])).toEqual({ div: 1e3, label: "K" });
  });
});

describe("formatBytes / formatVal", () => {
  it("scales bytes to the right unit", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2 KiB");
    expect(formatBytes(5 * 1048576)).toBe("5.0 MiB");
    expect(formatBytes(2 * 1073741824)).toBe("2.00 GiB");
  });

  it("formats a size column as bytes, a plain number otherwise", () => {
    expect(formatVal("bytes", 2048)).toBe("2 KiB");
    expect(formatVal("value", 3)).toBe("3");
    expect(formatVal("ratio", 1.23456)).toBe("1.235");
    expect(formatVal("name", "foo")).toBe("foo");
  });
});

describe("numericCols", () => {
  it("returns keys whose first-row value is a number", () => {
    expect(numericCols([{ run_id: "a", x: 1, label: "y" }])).toEqual(["x"]);
    expect(numericCols([])).toEqual([]);
  });
});

describe("assignColors", () => {
  it("assigns one color per name, cycling the palette", () => {
    const colors = assignColors(["a", "b"]);
    expect(Object.keys(colors)).toEqual(["a", "b"]);
    expect(colors.a).not.toBe(colors.b);
  });

  it("wraps around after 20 names", () => {
    const names = Array.from({ length: 21 }, (_, i) => `n${i}`);
    const colors = assignColors(names);
    expect(colors.n0).toBe(colors.n20);
  });
});

describe("binByX", () => {
  it("groups y values by x, skipping nulls", () => {
    const rows = [
      { x: 1, y: 10 },
      { x: 1, y: 20 },
      { x: 2, y: 30 },
      { x: null, y: 40 },
      { x: 3, y: null },
    ];
    expect(binByX(rows, "x", "y")).toEqual({ "1": [10, 20], "2": [30] });
  });
});
