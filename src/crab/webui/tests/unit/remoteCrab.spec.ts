import { describe, expect, it } from "vitest";
import { crabCommandView } from "@/lib/remoteCrab";

describe("crabCommandView", () => {
  it.each([null, undefined, "", "   "])(
    "treats %j as unset: checkout hint, install panel, null",
    (input) => {
      const v = crabCommandView(input, "~/hpc");
      expect(v.hint).toBe("Empty: the CRAB checkout in ~/hpc/CRAB");
      expect(v.showInstall).toBe(true);
      expect(v.payload).toBeNull();
      expect(v.error).toBeNull();
    },
  );

  it("falls back to ~ and drops trailing slashes in the install dir", () => {
    expect(crabCommandView("", "").hint).toBe("Empty: the CRAB checkout in ~/CRAB");
    expect(crabCommandView("", "/data//").hint).toBe("Empty: the CRAB checkout in /data/CRAB");
  });

  it("a set command gives a note, hides the install panel and is sent trimmed", () => {
    const v = crabCommandView("  ~/.local/bin/crab ", "~");
    expect(v.hint).toBe(
      "This cluster runs `~/.local/bin/crab` directly. You manage that install (pipx install crab-hpc).",
    );
    expect(v.showInstall).toBe(false);
    expect(v.payload).toBe("~/.local/bin/crab");
    expect(v.error).toBeNull();
  });

  it("a value with inner whitespace is an error and not savable", () => {
    const v = crabCommandView("pipx run crab", "~");
    expect(v.error).toBe(
      "Crab command must be one executable path or name with no spaces (for example ~/.local/bin/crab).",
    );
    expect(v.payload).toBe("pipx run crab");
  });
});
