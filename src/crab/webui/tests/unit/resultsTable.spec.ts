import { describe, expect, it } from "vitest";
import { defaultVisibleColumns, filterRows, sortRows } from "@/lib/resultsTable";

describe("defaultVisibleColumns", () => {
  it("preselects run_id, msg_size, and up to two avg* columns", () => {
    const cols = ["run_id", "msg_size", "avg_latency", "avg_bandwidth", "avg_jitter", "extra"];
    const visible = defaultVisibleColumns(cols);
    expect(visible).toEqual(new Set(["run_id", "msg_size", "avg_latency", "avg_bandwidth"]));
  });

  it("falls back to every column when the default selection is under 3", () => {
    const cols = ["value"];
    const visible = defaultVisibleColumns(cols);
    expect(visible.has("value")).toBe(true);
  });
});

describe("filterRows", () => {
  const rows = [
    { run_id: "run-1", label: "foo" },
    { run_id: "run-2", label: "bar" },
  ];

  it("keeps every row when the search is empty", () => {
    expect(filterRows(rows, ["run_id", "label"], "")).toEqual(rows);
  });

  it("matches case-insensitively across the given columns", () => {
    expect(filterRows(rows, ["run_id", "label"], "FOO")).toEqual([rows[0]]);
  });

  it("only searches the given columns, not the whole row", () => {
    expect(filterRows(rows, ["run_id"], "foo")).toEqual([]);
  });
});

describe("sortRows", () => {
  const rows = [
    { name: "b", n: 2 },
    { name: "a", n: 10 },
  ];

  it("returns the rows unchanged when no column is chosen", () => {
    expect(sortRows(rows, null, 1)).toEqual(rows);
  });

  it("sorts numeric columns numerically, not lexicographically", () => {
    expect(sortRows(rows, "n", 1).map((r) => r.n)).toEqual([2, 10]);
  });

  it("sorts string columns lexicographically", () => {
    expect(sortRows(rows, "name", 1).map((r) => r.name)).toEqual(["a", "b"]);
  });

  it("reverses order when dir is -1", () => {
    expect(sortRows(rows, "n", -1).map((r) => r.n)).toEqual([10, 2]);
  });

  it("does not mutate the input array", () => {
    const original = [...rows];
    sortRows(rows, "n", 1);
    expect(rows).toEqual(original);
  });
});
