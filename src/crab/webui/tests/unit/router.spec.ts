/**
 * Router table: unknown paths fall back to /sbatchman and every page names its
 * browser tab. The router is built with createWebHistory, which needs a DOM,
 * so the tests use the exported `routes` table and the pure `titleFor` helper.
 */
import { describe, expect, it } from "vitest";
import { routes, titleFor } from "@/router";

describe("routes", () => {
  it("ends with a catch-all that redirects to /sbatchman", () => {
    const last = routes[routes.length - 1];
    expect(last.path).toBe("/:pathMatch(.*)*");
    expect("redirect" in last && last.redirect).toBe("/sbatchman");
  });

  it("gives every named route a title", () => {
    const named = routes.filter((r) => "name" in r && r.name);
    expect(named.map((r) => ("name" in r ? r.name : ""))).toEqual([
      "sbatchman",
      "remotes",
      "wrappers",
    ]);
    for (const r of named) {
      expect(typeof (r.meta as { title?: unknown } | undefined)?.title).toBe("string");
    }
  });
});

describe("titleFor", () => {
  it("builds 'Page - CRAB' from meta.title", () => {
    expect(titleFor({ meta: { title: "SbatchMan" } })).toBe("SbatchMan - CRAB");
    expect(titleFor({ meta: { title: "Remotes" } })).toBe("Remotes - CRAB");
    expect(titleFor({ meta: { title: "Wrappers" } })).toBe("Wrappers - CRAB");
  });

  it("falls back to CRAB without a title", () => {
    expect(titleFor({ meta: {} })).toBe("CRAB");
    expect(titleFor({ meta: { title: "" } })).toBe("CRAB");
    expect(titleFor({ meta: { title: 3 } })).toBe("CRAB");
  });
});
