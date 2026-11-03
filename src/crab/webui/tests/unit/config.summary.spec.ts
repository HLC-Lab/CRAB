/** allocationSummary node counts and the {var} placeholder handling in allocation splits. */
import { describe, expect, it } from "vitest";

import { allocationSummary, emptyApp, fromAllocation, toAllocation } from "@/lib/config";

describe("allocationSummary badge math", () => {
  function app(path: string, partition?: string) {
    const a = emptyApp();
    a.path = path;
    if (partition) a.partition = partition;
    return a;
  }

  it("groups 50/50 on 8 nodes give 4 each", () => {
    const alloc = fromAllocation({
      mode: "linear",
      partitions: { victim: { share: 50 }, aggressor: { share: 50 } },
    });
    const sum = allocationSummary([app("a.py", "victim"), app("b.py", "aggressor")], alloc, "8");
    expect(sum[0].group).toBe("victim");
    expect(sum.map((s) => s.nodes)).toEqual([4, 4]);
  });

  it("by-app split [60,40] on 10 nodes gives 6 and 4", () => {
    const alloc = fromAllocation({ mode: "interleaved", split: [60, 40] });
    const sum = allocationSummary([app("a.py"), app("b.py")], alloc, "10");
    expect(sum.map((s) => s.nodes)).toEqual([6, 4]);
  });

  it("no allocation gives no counts and no group", () => {
    const sum = allocationSummary([app("a.py")], fromAllocation(undefined), "8");
    // Loose null check on purpose: the summary leaves these fields unset.
    expect(sum[0].nodes ?? null).toBeNull();
    expect(sum[0].group ?? null).toBeNull();
  });
});

describe("allocation placeholders", () => {
  it("a by-app split keeps {var} tokens next to numbers, in order", () => {
    const a = fromAllocation({ mode: "linear" });
    a.by = "app";
    a.split = "{left}, 50";
    expect(toAllocation(a)).toEqual({ mode: "linear", split: ["{left}", 50] });
  });
  it("a non-token, non-number value is still not emitted as a string", () => {
    const a = fromAllocation({ mode: "interleaved" });
    a.stride = "abc";
    expect(typeof toAllocation(a)?.stride).not.toBe("string");
  });
});
