/**
 * Wrapper attributes in the editor: rows of key and value <-> `AppDraft.rest`.
 * `rest` holds every app key the engine injects into the wrapper (QE `input_file`,
 * `pseudo_dir`); the reserved keys have their own fields and are refused as rows.
 */
import { describe, expect, it } from "vitest";

import { emptyApp, emptyDraft, emptyExperiment, fromConfig, toConfig } from "@/lib/config";
import { RESERVED_APP_KEYS } from "@/lib/config";
import { restToRows, rowsToRest, rowProblems } from "@/lib/wrapperSettings";

describe("wrapperSettings rows", () => {
  it("round trips string attributes in order", () => {
    const rest = { input_file: "pw.in", pseudo_dir: "/data/pseudo" };
    const rows = restToRows(rest);
    expect(rows).toEqual([
      { key: "input_file", value: "pw.in" },
      { key: "pseudo_dir", value: "/data/pseudo" },
    ]);
    expect(rowsToRest(rows, rest)).toEqual(rest);
  });

  it("drops a row with an empty key", () => {
    const rest = rowsToRest(
      [
        { key: "", value: "orphan" },
        { key: "  ", value: "blank" },
        { key: "input_file", value: "pw.in" },
      ],
      {},
    );
    expect(rest).toEqual({ input_file: "pw.in" });
  });

  it("trims keys", () => {
    expect(rowsToRest([{ key: " input_file ", value: "x" }], {})).toEqual({ input_file: "x" });
  });

  it("refuses every reserved key and keeps it out of rest", () => {
    for (const key of RESERVED_APP_KEYS) {
      expect(rowProblems([{ key, value: "x" }])[0]).toMatch(/own field|reserved/i);
      expect(rowsToRest([{ key, value: "x" }], {})).toEqual({});
    }
    expect(RESERVED_APP_KEYS).toContain("args");
    expect(rowProblems([{ key: "bind", value: "x" }])[0]).toMatch(/reserved/i);
  });

  it("flags a duplicate key and keeps the first", () => {
    const rows = [
      { key: "input_file", value: "a" },
      { key: "input_file", value: "b" },
    ];
    expect(rowProblems(rows)[0]).toBe("");
    expect(rowProblems(rows)[1]).toMatch(/twice|duplicate/i);
    expect(rowsToRest(rows, {})).toEqual({ input_file: "a" });
  });

  it("keeps a non-string value when its row text is untouched", () => {
    const rest = { threads: 4, flags: ["-a", "-b"], on: true };
    const rows = restToRows(rest);
    expect(rows.map((r) => r.value)).toEqual(["4", '["-a","-b"]', "true"]);
    expect(rowsToRest(rows, rest)).toEqual(rest);
    const edited = rows.map((r) => (r.key === "threads" ? { ...r, value: "8" } : r));
    expect(rowsToRest(edited, rest)).toEqual({ threads: "8", flags: ["-a", "-b"], on: true });
  });
});

describe("wrapperSettings and the engine config", () => {
  it("writes the attributes into the app entry and reads them back", () => {
    const draft = emptyDraft();
    const exp = emptyExperiment();
    exp.name = "qe";
    const app = emptyApp();
    app.path = "others/qe.py";
    app.rest = rowsToRest(
      [
        { key: "input_file", value: "pw.in" },
        { key: "pseudo_dir", value: "/data/pseudo" },
      ],
      {},
    );
    exp.apps.push(app);
    draft.experiments.push(exp);
    const cfg = toConfig(draft);
    const entry = cfg.experiments.qe.apps["0"];
    expect(entry.input_file).toBe("pw.in");
    expect(entry.pseudo_dir).toBe("/data/pseudo");
    const back = fromConfig(cfg);
    expect(restToRows(back.experiments[0].apps[0].rest)).toEqual([
      { key: "input_file", value: "pw.in" },
      { key: "pseudo_dir", value: "/data/pseudo" },
    ]);
  });
});
