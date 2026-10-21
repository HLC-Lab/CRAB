/** validateDraft against the target cluster's wrapper catalog (optional second argument). */
import { describe, expect, it } from "vitest";

import { emptyApp, emptyDraft, emptyExperiment, validateDraft } from "@/lib/config";

const CATALOG = new Set(["blink/alltoall/alltoall_b.py"]);

function draftWith(path: string) {
  const d = emptyDraft();
  d.numnodes = "2";
  const ex = emptyExperiment("ex1");
  const app = emptyApp();
  app.path = path;
  ex.apps.push(app);
  d.experiments = [ex];
  return d;
}

const wrapperIssues = (msgs: string[]) => msgs.filter((m) => /wrapper/.test(m));

describe("validateDraft wrapper catalog check", () => {
  it("runs no wrapper check without a catalog", () => {
    const d = draftWith("blink/a2a_b.py");
    expect(validateDraft(d)).toEqual(validateDraft(d, undefined));
    expect(wrapperIssues(validateDraft(d))).toEqual([]);
  });

  it("flags a path outside the catalog, naming the path and the cluster", () => {
    const issues = wrapperIssues(
      validateDraft(draftWith("blink/a2a_b.py"), { wrappers: CATALOG, cluster: "leonardo" }),
    );
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("blink/a2a_b.py");
    expect(issues[0]).toContain("is not on leonardo");
  });

  it("accepts a path that is in the catalog", () => {
    const d = draftWith("blink/alltoall/alltoall_b.py");
    expect(validateDraft(d, { wrappers: CATALOG, cluster: "leonardo" })).toEqual(validateDraft(d));
  });

  it("does not flag a {var} placeholder path or an absolute path", () => {
    for (const p of ["{wrapper}", "blink/{name}.py", "/home/x/w.py"]) {
      const issues = validateDraft(draftWith(p), { wrappers: CATALOG, cluster: "leonardo" });
      expect(wrapperIssues(issues)).toEqual([]);
    }
  });

  it("treats an empty catalog as unknown and runs no check", () => {
    const issues = validateDraft(draftWith("blink/a2a_b.py"), {
      wrappers: new Set(),
      cluster: "leonardo",
    });
    expect(wrapperIssues(issues)).toEqual([]);
  });

  it("does not double-report an empty path", () => {
    const issues = validateDraft(draftWith(""), { wrappers: CATALOG, cluster: "leonardo" });
    expect(wrapperIssues(issues)).toEqual(["ex1 · app #0: wrapper path is required."]);
  });
});
