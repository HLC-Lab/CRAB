/**
 * SbatchMan mode detection (plan 084): the flag is delivered as an injected
 * meta tag in production, with a localStorage dev fallback. When the meta is
 * present it is authoritative (localStorage must not override it).
 *
 * Vitest runs under the `node` environment (no real DOM), so `document` and
 * `localStorage` are stubbed per case, matching resultsPlot.spec.ts.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { isSbatchmanMode } from "@/lib/mode";

/** Stub just enough of `document` and `localStorage` for the helper. */
function stubEnv(metaContent: string | null, devFlag: string | null) {
  vi.stubGlobal("document", {
    querySelector: (sel: string) =>
      sel === 'meta[name="crab-sbatchman"]' && metaContent !== null
        ? { getAttribute: () => metaContent }
        : null,
  });
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => (k === "CRAB_DEV_SBATCHMAN" ? devFlag : null),
  });
}

describe("isSbatchmanMode", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is false with no meta and no dev flag", () => {
    stubEnv(null, null);
    expect(isSbatchmanMode()).toBe(false);
  });

  it("is true when the meta content is 'true'", () => {
    stubEnv("true", null);
    expect(isSbatchmanMode()).toBe(true);
  });

  it("is false when the meta content is 'false'", () => {
    stubEnv("false", null);
    expect(isSbatchmanMode()).toBe(false);
  });

  it("meta wins over the localStorage dev fallback", () => {
    stubEnv("false", "true");
    expect(isSbatchmanMode()).toBe(false);
  });

  it("falls back to localStorage when no meta is present (Vite dev)", () => {
    stubEnv(null, "true");
    expect(isSbatchmanMode()).toBe(true);
  });
});
