import { describe, expect, it } from "vitest";
import { needsLabel, presetsHint } from "@/lib/presetSetup";
import type { CrabInfo } from "@/api/types";

function info(extra: Partial<CrabInfo> = {}): CrabInfo {
  return { schema: 1, crab_version: "1.0.0", crab_root: "/crab", presets: [], ...extra };
}

describe("needsLabel", () => {
  it("names one missing option", () => {
    expect(needsLabel({ needs: ["--account"] })).toBe("needs setup: --account");
  });

  it("joins several options with a comma", () => {
    expect(needsLabel({ needs: ["--account", "--partition"] })).toBe(
      "needs setup: --account, --partition",
    );
  });

  it("is null for an empty or missing needs (older cluster)", () => {
    expect(needsLabel({ needs: [] })).toBeNull();
    expect(needsLabel({})).toBeNull();
  });
});

describe("presetsHint", () => {
  it("shows the loader error as an error", () => {
    expect(presetsHint(info({ presets_error: "bad toml in x" }))).toEqual({
      kind: "err",
      text: "bad toml in x",
    });
  });

  it("prefers the error over the no-clone hint", () => {
    const hint = presetsHint(
      info({ presets_error: "boom", presets_sources: [{ path: "/w", exists: false }] }),
    );
    expect(hint?.kind).toBe("err");
  });

  it("tells to run crab update when no shared presets folder exists", () => {
    const hint = presetsHint(
      info({
        presets_sources: [
          { path: "/a", exists: false },
          { path: "/b", exists: false },
        ],
      }),
    );
    expect(hint?.kind).toBe("warn");
    expect(hint?.text).toContain("No shared presets on this cluster");
    expect(hint?.text).toContain("crab update");
    expect(hint?.text).toContain("crab-wrappers");
  });

  it("is null when some folder exists", () => {
    const sources = [
      { path: "/a", exists: false },
      { path: "/b", exists: true },
    ];
    expect(presetsHint(info({ presets_sources: sources }))).toBeNull();
  });

  it("is null for an older cluster without presets_sources", () => {
    expect(presetsHint(info())).toBeNull();
  });
});
