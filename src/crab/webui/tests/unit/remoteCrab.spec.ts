import { describe, expect, it } from "vitest";
import { clusterNameHint, crabCommandView, installDirHint } from "@/lib/remoteCrab";

describe("crabCommandView", () => {
  it.each([null, undefined, "", "   "])(
    "treats %j as unset: checkout hint, install panel, null",
    (input) => {
      const v = crabCommandView(input, "~/hpc");
      expect(v.hint).toContain("uses the CRAB checkout in ~/hpc/CRAB");
      expect(v.hint).toContain("only for a CRAB you installed yourself");
      expect(v.hint).toContain("~/.local/bin/crab");
      expect(v.showInstall).toBe(true);
      expect(v.payload).toBeNull();
      expect(v.error).toBeNull();
    },
  );

  it("falls back to ~ and drops trailing slashes in the install dir", () => {
    expect(crabCommandView("", "").hint).toContain("the CRAB checkout in ~/CRAB.");
    expect(crabCommandView("", "/data//").hint).toContain("the CRAB checkout in /data/CRAB.");
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

describe("installDirHint", () => {
  it("in SbatchMan mode names the umbrella folder installed with ./install.sh", () => {
    const hint = installDirHint("~/crab-sbatchman", true, true);
    expect(hint).toContain("crab-sbatchman");
    expect(hint).toContain("already installed");
    expect(hint).toContain("./install.sh");
    expect(hint).toContain("CRAB is in ~/crab-sbatchman/CRAB");
  });

  it("in SbatchMan mode with your own Crab command leaves the checkout out", () => {
    const hint = installDirHint("~/x", true, false);
    expect(hint).toContain("./install.sh");
    expect(hint).not.toContain("~/x/CRAB");
  });

  it("outside SbatchMan mode says Save stores the profile only", () => {
    const hint = installDirHint("~/hpc/", false, true);
    expect(hint).toContain("~/hpc/CRAB");
    expect(hint).toContain("Save only stores");
    expect(hint).toContain("Install CRAB");
    expect(hint).not.toContain("./install.sh");
  });

  it("is empty outside SbatchMan mode when your own command is set", () => {
    expect(installDirHint("~", false, false)).toBe("");
  });

  it("uses no em dash", () => {
    for (const hint of [installDirHint("~", true, true), installDirHint("~", false, true)]) {
      expect(hint).not.toContain("\u2014");
    }
  });
});

describe("clusterNameHint", () => {
  it("in SbatchMan mode says the name is only a laptop label nothing else reads", () => {
    const hint = clusterNameHint(true);
    expect(hint).toContain("label");
    expect(hint).toContain("on your laptop");
    expect(hint).toContain("Nothing on the cluster or in SbatchMan reads it");
    expect(hint).not.toContain("\u2014");
  });

  it("is empty outside SbatchMan mode", () => {
    expect(clusterNameHint(false)).toBe("");
  });
});
