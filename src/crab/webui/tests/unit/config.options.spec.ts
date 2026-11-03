/** Launcher options, per-app and per-experiment ppn, per-app env and cpus, and optionsAreSet. */
import { describe, expect, it } from "vitest";

import {
  cloneExperiment,
  emptyApp,
  emptyDraft,
  emptyExperiment,
  emptyOptions,
  fromConfig,
  hasLocalOverrides,
  optionsAreSet,
  toConfig,
  validateDraft,
} from "@/lib/config";

// Fixtures are raw engine-JSON shapes; typing them defeats the point of
// exercising untyped hand-written configs.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

describe("launcher options (ADR-033)", () => {
  const cfg = (global: AnyObj, local?: AnyObj): AnyObj => ({
    global_options: { numnodes: "2", ...global },
    experiments: {
      ex1: { apps: { 0: { path: "a.py" } }, ...(local ? { local_options: local } : {}) },
    },
  });

  it("reads both keys into draft fields, global and per experiment", () => {
    const d = fromConfig(
      cfg(
        { launcher: "mpirun", launcher_flags: ["-v", "--x=1"] },
        { launcher: "direct", launcher_flags: [] },
      ),
    );
    expect(d.options.launcher).toBe("mpirun");
    expect(d.options.replaceLauncherFlags).toBe(true);
    expect(d.options.launcherFlags).toBe("-v --x=1");
    expect(d.experiments[0].options.launcher).toBe("direct");
    expect(d.experiments[0].options.replaceLauncherFlags).toBe(true);
    expect(d.experiments[0].options.launcherFlags).toBe("");
    // Neither key may leak into the wrapper-attribute passthrough.
    expect(d.experiments[0].apps[0].rest).toEqual({});
  });

  it("an empty launcher choice emits no launcher key", () => {
    const d = emptyDraft();
    d.options.launcher = "";
    expect("launcher" in toConfig(d).global_options).toBe(false);
  });

  it("an unchecked Replace emits no launcher_flags, whatever the text says", () => {
    const d = emptyDraft();
    d.options.replaceLauncherFlags = false;
    d.options.launcherFlags = "--stale";
    expect("launcher_flags" in toConfig(d).global_options).toBe(false);
  });

  it("checked with empty text emits [], with text emits the tokens", () => {
    const d = emptyDraft();
    d.options.replaceLauncherFlags = true;
    expect(toConfig(d).global_options.launcher_flags).toEqual([]);
    d.options.launcherFlags = "  --a   -b=2 ";
    expect(toConfig(d).global_options.launcher_flags).toEqual(["--a", "-b=2"]);
  });

  it("an untouched experiment emits no launcher keys in local_options", () => {
    const d = fromConfig(cfg({}));
    expect("local_options" in toConfig(d).experiments.ex1).toBe(false);
  });

  it("a valid launcher and flag list pass validation", () => {
    const d = fromConfig(cfg({ launcher: "srun", launcher_flags: ["--exact"] }));
    expect(validateDraft(d).filter((m: string) => /launcher/.test(m))).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Ranks per node per app and per experiment (ADR-036 decision 3).
// ---------------------------------------------------------------------------
describe("per-app and per-experiment ppn", () => {
  const cfg = (app: AnyObj, local: AnyObj = {}): AnyObj => ({
    global_options: { numnodes: "4", ppn: "1" },
    experiments: {
      ex1: {
        local_options: local,
        apps: { 0: { path: "a.py", args: "", collect: true, start: "0", end: "", ...app } },
      },
    },
  });

  it("reads an app's ppn into its draft field, not into rest", () => {
    const app = fromConfig(cfg({ ppn: "4", extra: "x" })).experiments[0].apps[0];
    expect(app.ppn).toBe("4");
    expect(app.rest).toEqual({ extra: "x" });
  });

  it("reads an experiment's local ppn into its draft field", () => {
    expect(fromConfig(cfg({}, { ppn: "2" })).experiments[0].ppn).toBe("2");
  });

  it("emits app ppn 4 and experiment ppn 2 the way the global one is emitted", () => {
    const out = toConfig(fromConfig(cfg({ ppn: "4" }, { ppn: "2" })));
    expect(out.experiments.ex1.apps[0].ppn).toBe("4");
    expect(out.experiments.ex1.local_options).toEqual({ ppn: "2" });
  });

  it("emits no ppn key when the fields are empty or blank", () => {
    const d = fromConfig(cfg({}));
    d.experiments[0].apps[0].ppn = "  ";
    d.experiments[0].ppn = " ";
    const out = toConfig(d).experiments.ex1;
    expect("ppn" in out.apps[0]).toBe(false);
    expect(out.local_options).toBeUndefined();
  });

  it("trims on emit", () => {
    const d = fromConfig(cfg({}));
    d.experiments[0].apps[0].ppn = " 4 ";
    d.experiments[0].ppn = " 2 ";
    const out = toConfig(d).experiments.ex1;
    expect(out.apps[0].ppn).toBe("4");
    expect(out.local_options).toEqual({ ppn: "2" });
  });

  it("an app and an experiment made empty have no ppn", () => {
    expect(emptyApp().ppn).toBe("");
    expect(emptyExperiment("x").ppn).toBe("");
  });

  it("a cloned experiment keeps both ppn values", () => {
    const d = fromConfig(cfg({ ppn: "4" }, { ppn: "2" }));
    const copy = cloneExperiment(d.experiments[0]);
    expect(copy.ppn).toBe("2");
    expect(copy.apps[0].ppn).toBe("4");
  });

  it("an experiment with only a ppn override counts as overriding", () => {
    expect(hasLocalOverrides(fromConfig(cfg({}, { ppn: "2" })).experiments[0])).toBe(true);
    expect(hasLocalOverrides(fromConfig(cfg({}, {})).experiments[0])).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Per-app env and cpus: reserved app keys, edited in the app card.
// ---------------------------------------------------------------------------
describe("per-app env and cpus", () => {
  const withApp = (app: AnyObj): AnyObj => ({
    global_options: { numnodes: "4" },
    experiments: { ex1: { apps: { 0: { path: "a.py", args: "", collect: true, ...app } } } },
  });
  const appOut = (config: AnyObj): AnyObj => toConfig(fromConfig(config)).experiments.ex1.apps[0];

  it("round-trips env, in key order", () => {
    const env = { NCCL_IB_HCA: "mlx5_0", UCX_TLS: "rc" };
    const out = appOut(withApp({ env }));
    expect(out.env).toEqual(env);
    expect(Object.keys(out.env)).toEqual(["NCCL_IB_HCA", "UCX_TLS"]);
  });

  it("round-trips cpus", () => {
    expect(appOut(withApp({ cpus: "0-3,8" })).cpus).toBe("0-3,8");
  });

  it("round-trips both together", () => {
    const out = appOut(withApp({ env: { A: "1" }, cpus: "0-15,32" }));
    expect(out.env).toEqual({ A: "1" });
    expect(out.cpus).toBe("0-15,32");
  });

  it("reads them into draft fields, not into rest", () => {
    const app = fromConfig(withApp({ env: { A: "1" }, cpus: "0-3", extra: "x" })).experiments[0]
      .apps[0];
    expect(app.env).toEqual([{ key: "A", value: "1" }]);
    expect(app.cpus).toBe("0-3");
    expect(app.rest).toEqual({ extra: "x" });
  });

  it("keeps nics in the app draft's rest and round-trips it in order", () => {
    const nics = ["mlx5_0:1", "mlx5_1:1"];
    const config = withApp({ nics });
    expect(fromConfig(config).experiments[0].apps[0].rest).toEqual({ nics });
    expect(appOut(config).nics).toEqual(nics);
  });

  it("emits neither key for an app that sets none", () => {
    const out = appOut(withApp({}));
    expect("env" in out).toBe(false);
    expect("cpus" in out).toBe(false);
  });

  it("emits neither key when the editor rows are blank", () => {
    const d = fromConfig(withApp({}));
    d.experiments[0].apps[0].env = [{ key: "  ", value: "" }];
    d.experiments[0].apps[0].cpus = "  ";
    const out = toConfig(d).experiments.ex1.apps[0];
    expect("env" in out).toBe(false);
    expect("cpus" in out).toBe(false);
  });

  it("trims keys and cpus on emit, keeps values as typed", () => {
    const d = fromConfig(withApp({}));
    d.experiments[0].apps[0].env = [{ key: " A ", value: " v w " }];
    d.experiments[0].apps[0].cpus = " 0-3 ";
    const out = toConfig(d).experiments.ex1.apps[0];
    expect(out.env).toEqual({ A: " v w " });
    expect(out.cpus).toBe("0-3");
  });

  it("an app made with emptyApp has no env rows and no cpus", () => {
    const app = emptyApp();
    expect(app.env).toEqual([]);
    expect(app.cpus).toBe("");
  });

  it("flags a repeated env name and a value without a name", () => {
    const d = fromConfig(withApp({}));
    d.experiments[0].apps[0].env = [
      { key: "A", value: "1" },
      { key: "A", value: "2" },
      { key: "", value: "3" },
    ];
    const msgs = validateDraft(d).filter((m: string) => /env/.test(m));
    expect(msgs).toEqual([
      "ex1 · app #0: env: 'A' is listed twice",
      "ex1 · app #0: env: a value needs a variable name",
    ]);
  });
});

describe("optionsAreSet", () => {
  it("is false for untouched options", () => {
    expect(optionsAreSet(emptyOptions())).toBe(false);
  });

  it("is true once a string option is set", () => {
    const o = emptyOptions();
    o.walltime = "00:10:00";
    expect(optionsAreSet(o)).toBe(true);
  });

  it("is true with a launcher chosen", () => {
    const o = emptyOptions();
    o.launcher = "srun";
    expect(optionsAreSet(o)).toBe(true);
  });

  it("is true when launcher flags are replaced, even with none (emits [])", () => {
    const o = emptyOptions();
    o.replaceLauncherFlags = true;
    expect(optionsAreSet(o)).toBe(true);
  });

  it("is true when launcher flags are carried raw", () => {
    const o = emptyOptions();
    o.launcherFlagsRaw = ["--bind-to", "core"];
    expect(optionsAreSet(o)).toBe(true);
  });
});
