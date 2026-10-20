/**
 * Values the engine accepts that the editor must keep meaning-for-meaning. Expected values come
 * from the engine source cited in each describe, not from config.ts.
 */
import { describe, expect, it } from "vitest";

import {
  fromAllocation,
  fromConfig,
  fromSbatch,
  toConfig,
  toSbatch,
  validateAllocation,
  validateDraft,
  validateSbatch,
} from "@/lib/config";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- engine JSON is untyped here
type AnyObj = Record<string, any>;

const oneApp = (app: AnyObj, global: AnyObj = {}): AnyObj => ({
  global_options: { numnodes: "4", ...global },
  experiments: { ex1: { apps: { 0: { path: "a.py", ...app } } } },
});

describe("text booleans (core/config_checks.py parse_bool: 'true'/'false' in any case)", () => {
  it("keeps convergeall and retain_files false when they arrive as text", () => {
    const round = toConfig(fromConfig(oneApp({}, { convergeall: "false", retain_files: "False" })));
    expect(round.global_options.convergeall).toBe(false);
    expect(round.global_options.retain_files).toBe(false);
  });

  it("keeps text true as true", () => {
    const round = toConfig(fromConfig(oneApp({}, { convergeall: "TRUE", retain_files: "true" })));
    expect(round.global_options.convergeall).toBe(true);
    expect(round.global_options.retain_files).toBe(true);
  });
});

describe("text collect (core/experiment/runner.py reads collect with parse_bool)", () => {
  it("keeps an app measured when collect arrives as text", () => {
    const round = toConfig(fromConfig(oneApp({ collect: "True" })));
    expect(round.experiments.ex1.apps[0].collect).toBe(true);
  });
});

describe("placeholder tokens in a legacy split (SbatchMan substitutes {name} before the engine)", () => {
  it("keeps tokens as group shares, in order, next to numbers", () => {
    const config = oneApp({}, { allocation: { mode: "linear", split: ["{a}", 50] } });
    config.experiments.ex1.apps[1] = { path: "b.py" };
    const round = toConfig(fromConfig(config));
    expect(round.global_options.allocation.partitions).toEqual({
      group_1: { share: "{a}" },
      group_2: { share: 50 },
    });
  });
});

describe("placeholder tokens pass validation where they are saved (toAllocation keeps them)", () => {
  it("accepts {name} tokens in group shares", () => {
    const a = fromAllocation({
      mode: "linear",
      partitions: { v: { share: "{s}" }, x: { share: "{t}" } },
    });
    expect(validateAllocation(a).issues).toEqual([]);
  });

  it("accepts {name} tokens in a by-app split, stride and seed", () => {
    const split = fromAllocation({ mode: "linear", split: ["{a}", 50] });
    split.by = "app";
    split.split = "{a}, 50";
    const stride = fromAllocation({ mode: "interleaved", stride: "{k}" });
    const seed = fromAllocation({ mode: "random", seed: "{r}" });
    expect([split, stride, seed].flatMap((a) => validateAllocation(a).issues)).toEqual([]);
  });

  it("still rejects text that is neither a number nor a token", () => {
    const a = fromAllocation({ mode: "linear", partitions: { v: { share: "half" } } });
    expect(validateAllocation(a).issues).toEqual(['node group "v": share must be a number.']);
  });
});

describe("sbatch directives follow the engine (core/execution/launch_checks.py MANAGED_DIRECTIVES)", () => {
  const warn = (lines: string[]) => validateSbatch(fromSbatch(lines));
  const useNumnodes = (text: string, key: string) =>
    `sbatch_directives: '${text}' sets '${key}', which CRAB manages itself: use numnodes`;
  const tasks = "CRAB sets the task count from numnodes and ppn";

  // The engine refuses these (the editor used to only warn that they would be ignored).
  it("refuses nodes, N, ntasks-per-node, ntasks and n with the engine's message", () => {
    expect(warn(["--nodes=2"])).toEqual([useNumnodes("--nodes=2", "nodes")]);
    expect(warn(["-N 2"])).toEqual([useNumnodes("-N 2", "N")]);
    expect(warn(["--ntasks-per-node=4"])).toEqual([
      "sbatch_directives: '--ntasks-per-node=4' sets 'ntasks-per-node', which CRAB manages itself: use ppn",
    ]);
    expect(warn(["--ntasks=8"])).toEqual([
      `sbatch_directives: '--ntasks=8' sets 'ntasks', which CRAB manages itself: ${tasks}`,
    ]);
    expect(warn(["-n 8"])).toEqual([
      `sbatch_directives: '-n 8' sets 'n', which CRAB manages itself: ${tasks}`,
    ]);
  });

  it("refuses a managed directive written in the dict form too", () => {
    const d = fromSbatch({ nodes: "2", exclusive: true });
    expect(validateSbatch(d)).toHaveLength(1);
  });

  it("warns on the short log flags -o and -e like --output and --error", () => {
    expect(warn(["-o out.log", "-e err.log"])).toEqual([
      'Slurm directive "-o" overrides CRAB\'s log redirection (allowed, but be aware).',
      'Slurm directive "-e" overrides CRAB\'s log redirection (allowed, but be aware).',
    ]);
    expect(warn(["--output=o.log", "--error=e.log"])).toHaveLength(2);
  });

  it("leaves directives the engine passes through alone", () => {
    expect(warn(["--exclusive", "-J myjob", "--time=00:10:00"])).toEqual([]);
  });
});

describe("sbatch list form keeps short flags as written (Slurm takes -J name, -o file)", () => {
  it("does not add dashes to a directive that already has one", () => {
    expect(toSbatch(fromSbatch(["-J myjob", "--exclusive", "time=00:10:00"]))).toEqual([
      "-J myjob",
      "--exclusive",
      "--time=00:10:00",
    ]);
  });
});

describe("launcher options (core/execution/launch_checks.py check_launcher_options)", () => {
  const withOptions = (global: AnyObj, local?: AnyObj): AnyObj => ({
    global_options: { numnodes: "4", ...global },
    experiments: {
      ex1: { apps: { 0: { path: "a.py" } }, ...(local ? { local_options: local } : {}) },
    },
  });
  const launcherIssues = (config: AnyObj) =>
    validateDraft(fromConfig(config)).filter((m) => /launcher/.test(m));

  it("refuses a launcher outside srun, mpirun, direct", () => {
    expect(launcherIssues(withOptions({ launcher: "pbs" }))).toEqual([
      "launcher 'pbs' must be one of srun, mpirun, direct.",
    ]);
  });

  it("names the experiment when the bad launcher is a local override", () => {
    expect(launcherIssues(withOptions({}, { launcher: "pbs" }))).toEqual([
      "Experiment \"ex1\": launcher 'pbs' must be one of srun, mpirun, direct.",
    ]);
  });

  it("refuses launcher_flags that are not a list of strings", () => {
    const msg = "launcher_flags must be a list of strings.";
    expect(launcherIssues(withOptions({ launcher_flags: ["-v", 3] }))).toEqual([msg]);
    expect(launcherIssues(withOptions({ launcher_flags: "-v" }))).toEqual([msg]);
  });

  it("keeps a non-string flag in the config until the user edits it", () => {
    const round = toConfig(fromConfig(withOptions({ launcher_flags: ["-v", 3] })));
    expect(round.global_options.launcher_flags).toEqual(["-v", 3]);
  });

  it("skips both checks on {name} placeholders, like the engine", () => {
    expect(launcherIssues(withOptions({ launcher: "{l}", launcher_flags: "{f}" }))).toEqual([]);
    const round = toConfig(fromConfig(withOptions({ launcher: "{l}", launcher_flags: "{f}" })));
    expect(round.global_options.launcher).toBe("{l}");
    expect(round.global_options.launcher_flags).toBe("{f}");
  });
});

describe("shared allocation (core/config_checks.py _check_shared_scope)", () => {
  const issues = (allocation: AnyObj, local = false): string[] =>
    validateDraft(
      fromConfig(
        local
          ? {
              global_options: { numnodes: "4" },
              experiments: {
                ex1: { apps: { 0: { path: "a.py" } }, local_options: { allocation } },
              },
            }
          : oneApp({}, { allocation }),
      ),
    ).filter((m) => /allocation/.test(m));

  it("keeps mode shared instead of turning it into linear", () => {
    expect(fromAllocation({ mode: "shared" }).mode).toBe("shared");
    expect(fromAllocation({ mode: "shared", cores: "shared" }).cores).toBe("shared");
  });

  it("accepts shared with either cores value", () => {
    expect(issues({ mode: "shared" })).toEqual([]);
    expect(issues({ mode: "shared", cores: "disjoint" })).toEqual([]);
    expect(issues({ mode: "shared", cores: "shared" })).toEqual([]);
  });

  it("refuses split, stride and seed next to shared", () => {
    expect(issues({ mode: "shared", split: [50, 50] })).toEqual([
      "allocation: split cannot be used with mode 'shared' (every app gets all the nodes)",
    ]);
    expect(issues({ mode: "shared", stride: 2 })).toEqual([
      "allocation: stride cannot be used with mode 'shared' (every app gets all the nodes)",
    ]);
    expect(issues({ mode: "shared", seed: 7 })).toEqual([
      "allocation: seed cannot be used with mode 'shared' (every app gets all the nodes)",
    ]);
  });

  it("refuses cores without shared", () => {
    expect(issues({ mode: "linear", cores: "shared" })).toEqual([
      "allocation: cores is only valid with mode 'shared'",
    ]);
  });

  it("refuses a cores value outside disjoint and shared", () => {
    expect(issues({ mode: "shared", cores: "pinned" })).toEqual([
      "allocation: cores 'pinned' is not one of: disjoint, shared",
    ]);
  });

  it("refuses a top-level shared mode next to partitions", () => {
    expect(issues({ mode: "shared", partitions: { v: {}, a: {} } })).toEqual([
      "allocation.mode 'shared' cannot be used with partitions " +
        "(the top-level mode lays out the partition blocks); " +
        "set mode 'shared' inside a partition instead",
    ]);
  });

  it("names the experiment when the shared allocation is a local override", () => {
    expect(issues({ mode: "shared", cores: "pinned" }, true)).toEqual([
      "Experiment \"ex1\": allocation: cores 'pinned' is not one of: disjoint, shared",
    ]);
  });

  it("skips the cores checks on a {name} placeholder, like the engine", () => {
    expect(issues({ mode: "shared", cores: "{c}" })).toEqual([]);
    expect(issues({ mode: "linear", cores: "{c}" })).toEqual([]);
  });

  it("allows a partition to share its own nodes", () => {
    expect(
      issues({ mode: "linear", partitions: { v: {}, a: { mode: "shared", cores: "shared" } } }),
    ).toEqual([]);
  });
});

describe("ppn per app and per experiment (ADR-036; same rule as the global ppn)", () => {
  const ppnIssues = (app: AnyObj, local: AnyObj = {}): string[] => {
    const config = oneApp(app);
    config.experiments.ex1.local_options = local;
    return validateDraft(fromConfig(config)).filter((m) => /processes per node/.test(m));
  };

  it("accepts a positive integer or nothing", () => {
    expect(ppnIssues({ ppn: "4" }, { ppn: "2" })).toEqual([]);
    expect(ppnIssues({}, {})).toEqual([]);
    expect(ppnIssues({ ppn: "" }, { ppn: "" })).toEqual([]);
  });

  it("refuses a non-positive-integer app ppn, naming the app", () => {
    const msg = "ex1 · app #0: processes per node must be a positive integer.";
    expect(ppnIssues({ ppn: "0" })).toEqual([msg]);
    expect(ppnIssues({ ppn: "x" })).toEqual([msg]);
  });

  it("refuses a non-positive-integer experiment ppn, naming the experiment", () => {
    const msg = 'Experiment "ex1": processes per node must be a positive integer.';
    expect(ppnIssues({}, { ppn: "-1" })).toEqual([msg]);
    expect(ppnIssues({}, { ppn: "0" })).toEqual([msg]);
  });
});

describe("per-app cpus (core/execution/placement.py parse_cpus)", () => {
  const cpusIssues = (cpus: string): string[] =>
    validateDraft(fromConfig(oneApp({ cpus }))).filter((m) => /cpus/.test(m));

  it("accepts N, A-B and comma lists", () => {
    expect(cpusIssues("0-3,8")).toEqual([]);
    expect(cpusIssues("5")).toEqual([]);
    expect(cpusIssues("0-15, 32")).toEqual([]);
    expect(cpusIssues("2-2")).toEqual([]);
  });

  it("refuses a reversed span, text, a negative number and a repeated core", () => {
    const at = "ex1 · app #0: cpus";
    expect(cpusIssues("3-1")).toEqual([
      `${at} '3-1' has the invalid item '3-1'; use N or A-B (A <= B), comma-separated`,
    ]);
    expect(cpusIssues("a")).toEqual([
      `${at} 'a' has the invalid item 'a'; use N or A-B (A <= B), comma-separated`,
    ]);
    expect(cpusIssues("-1")).toEqual([
      `${at} '-1' has the invalid item '-1'; use N or A-B (A <= B), comma-separated`,
    ]);
    expect(cpusIssues("1,1")).toEqual([`${at} '1,1' lists a core more than once`]);
    expect(cpusIssues("0-3,2")).toEqual([`${at} '0-3,2' lists a core more than once`]);
  });

  it("refuses an empty item between commas", () => {
    expect(cpusIssues("1,,2")).toHaveLength(1);
  });

  it("skips a {name} placeholder, like the engine", () => {
    expect(cpusIssues("0-{n}")).toEqual([]);
  });
});

describe("per-app env (core/execution/placement.py parse_app_env, settings.py DROPPED_KEYS)", () => {
  const envIssues = (env: AnyObj): string[] =>
    validateDraft(fromConfig(oneApp({ env }))).filter((m) => /env/.test(m));

  it("accepts shell variable names", () => {
    expect(envIssues({ NCCL_IB_HCA: "mlx5_0", _x1: "", UCX_TLS: "rc" })).toEqual([]);
  });

  it("refuses a key that is not a shell variable name", () => {
    expect(envIssues({ "A B": "1" })).toEqual([
      "ex1 · app #0: env: key 'A B' is not a valid shell variable name",
    ]);
    expect(envIssues({ "1A": "1" })).toHaveLength(1);
  });

  it("refuses a dropped CRAB_* key and names what replaces it", () => {
    expect(envIssues({ CRAB_MPIRUN: "mpirun" })).toEqual([
      "ex1 · app #0: env: CRAB_MPIRUN is no longer read; use launchers.mpirun.command",
    ]);
    expect(envIssues({ CRAB_WL_MANAGER: "srun" })).toEqual([
      "ex1 · app #0: env: CRAB_WL_MANAGER is no longer read; use launcher",
    ]);
    expect(envIssues({ CRAB_PINNING_FLAGS: "x" })[0]).toMatch(/launchers\.srun\.flags/);
  });

  it("does not refuse other CRAB_ variables", () => {
    expect(envIssues({ CRAB_MINE: "1" })).toEqual([]);
  });

  it("skips a {name} placeholder, like the engine", () => {
    expect(envIssues({ "A B": "{v}" })).toEqual([]);
  });
});
