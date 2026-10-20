/**
 * Config round-trip suite: engine JSON -> Draft -> engine JSON must be lossless
 * over every field the editor handles.
 *
 * project() strips keys the editor does NOT yet round-trip, via explicit
 * allow-sets (GLOBAL_KEYS / LOCAL_KEYS). When a new field becomes lossless,
 * ADD IT to the allow-set in the same change — that is what makes a future
 * accidental drop FAIL here instead of being silently projected away.
 *
 * Split-form allocations normalize to partitions on import (ADR-007), so their
 * "losslessness" is SEMANTIC, checked via the semanticAlloc canonicalizer.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  allocationSummary,
  cloneExperiment,
  emptyApp,
  emptyDraft,
  emptyExperiment,
  emptyOptions,
  fromAllocation,
  fromConfig,
  hasLocalOverrides,
  normalizeSplitToPartitions,
  optionsAreSet,
  toAllocation,
  toConfig,
  validateDraft,
} from "@/lib/config";

const REPO_ROOT = fileURLToPath(new URL("../../../../..", import.meta.url));

// ---------------------------------------------------------------------------
// Projection: carry ONLY the handled keys through the comparison.
// ---------------------------------------------------------------------------
const OPTION_KEYS = [
  "minruns",
  "maxruns",
  "timeout",
  "convergeall",
  "alpha",
  "beta",
  "outformat",
  "retain_files",
  "tags",
  "extrainfo",
  "walltime",
  "datapath",
  "launcher",
  "launcher_flags",
];
const GLOBAL_KEYS = new Set([
  "name",
  "numnodes",
  "ppn",
  "allocation",
  "sbatch_directives",
  ...OPTION_KEYS,
]);
// numnodes/name are job-level only and never valid in local_options; ppn may be overridden there.
const LOCAL_KEYS = new Set(["allocation", "ppn", ...OPTION_KEYS]);

// Fixtures are raw engine-JSON shapes; typing them defeats the point of
// exercising untyped hand-written configs.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

function pick(obj: AnyObj | undefined, keys: Set<string>): AnyObj {
  const out: AnyObj = {};
  for (const [k, v] of Object.entries(obj ?? {})) if (keys.has(k)) out[k] = v;
  return out;
}

function project(config: AnyObj): AnyObj {
  const exps: AnyObj = {};
  for (const [k, e] of Object.entries((config.experiments ?? {}) as AnyObj)) {
    const apps: AnyObj = {};
    for (const [ak, app] of Object.entries((e.apps ?? {}) as AnyObj)) {
      // Carry ALL app keys (incl. wrapper-attribute extras) so a dropped key
      // FAILS; normalize only the reserved fields to the editor's canonical values.
      const a: AnyObj = { ...app };
      a.path = app.path ?? "";
      a.args = app.args ?? "";
      a.collect = app.collect === true; // doc default is false
      a.start = app.start ?? "0";
      a.end = app.end ?? "";
      if (a.partition == null || a.partition === "") delete a.partition;
      apps[ak] = a;
    }
    const ex: AnyObj = { apps };
    if (e.description) ex.description = e.description;
    const lo = pick(e.local_options, LOCAL_KEYS);
    if (Object.keys(lo).length) ex.local_options = lo;
    exps[k] = ex;
  }
  return { global_options: pick(config.global_options, GLOBAL_KEYS), experiments: exps };
}

// ---------------------------------------------------------------------------
// Semantic allocation canonicalizer: split-form and partitions-form allocations
// that mean the same thing compare equal (split[i] == share-by-order; omitted /
// even share == 100/N; app order <-> partition tag).
// ---------------------------------------------------------------------------
function canonShares(alloc: AnyObj | undefined): number[] | null {
  if (!alloc || typeof alloc !== "object") return null;
  if (Array.isArray(alloc.split)) return alloc.split.map(Number);
  if (alloc.partitions && typeof alloc.partitions === "object") {
    const vals = Object.values(alloc.partitions as AnyObj);
    const n = vals.length || 1;
    return vals.map((v: AnyObj) => (v && v.share != null ? Number(v.share) : 100 / n));
  }
  return null;
}
function canonAppGroups(alloc: AnyObj | undefined, apps: AnyObj): (number | null)[] {
  const keys = alloc && alloc.partitions ? Object.keys(alloc.partitions) : [];
  return Object.values(apps ?? {}).map((a: AnyObj) => {
    const p = a?.partition;
    return p == null || p === "" ? null : keys.indexOf(p);
  });
}
function semanticAlloc(config: AnyObj): AnyObj {
  const g = config.global_options ?? {};
  const experiments: AnyObj = {};
  for (const [k, e] of Object.entries((config.experiments ?? {}) as AnyObj)) {
    const lo = e.local_options ?? {};
    const eff = lo.allocation != null ? lo.allocation : g.allocation;
    experiments[k] = {
      localShares: canonShares(lo.allocation),
      apps: canonAppGroups(eff, e.apps),
    };
  }
  return { shares: canonShares(g.allocation), experiments };
}

// ---------------------------------------------------------------------------
// Synthetic fixtures
// ---------------------------------------------------------------------------
const baseExp = {
  experiments: {
    ex1: {
      apps: {
        0: {
          path: "blink/a2a.py",
          args: "-n 1",
          collect: true,
          start: "0",
          end: "",
          partition: "victim",
        },
        1: {
          path: "others/g500.py",
          args: "",
          collect: false,
          start: "0",
          end: "f",
          partition: "aggressor",
        },
      },
    },
  },
};

const sharedExp = {
  ex1: {
    apps: {
      0: { path: "blink/a2a.py", args: "", collect: true, start: "0", end: "" },
      1: { path: "others/g500.py", args: "", collect: false, start: "0", end: "f" },
    },
  },
};

const fixtures: { name: string; config: AnyObj }[] = [
  {
    name: "shared allocation (mode only)",
    config: {
      global_options: { numnodes: "4", allocation: { mode: "shared" } },
      experiments: sharedExp,
    },
  },
  {
    name: "shared allocation with shared cores",
    config: {
      global_options: { numnodes: "4", allocation: { mode: "shared", cores: "shared" } },
      experiments: sharedExp,
    },
  },
  {
    name: "shared allocation with cores disjoint kept as loaded",
    config: {
      global_options: { numnodes: "4", allocation: { mode: "shared", cores: "disjoint" } },
      experiments: sharedExp,
    },
  },
  {
    name: "partition with its own shared mode and cores (kept in rest)",
    config: {
      global_options: {
        numnodes: "8",
        allocation: {
          mode: "linear",
          partitions: {
            victim: { share: 50 },
            aggressor: { share: 50, mode: "shared", cores: "shared" },
          },
        },
      },
      ...baseExp,
    },
  },
  {
    name: "shared allocation in an experiment's local_options",
    config: {
      global_options: { numnodes: "4" },
      experiments: {
        ex1: {
          local_options: { allocation: { mode: "shared", cores: "shared" } },
          apps: sharedExp.ex1.apps,
        },
      },
    },
  },
  {
    name: "empty partitions (equal split) re-emit {}",
    config: {
      global_options: {
        numnodes: "64",
        ppn: "1",
        allocation: { mode: "linear", partitions: { victim: {}, aggressor: {} } },
      },
      ...baseExp,
    },
  },
  {
    name: "shared partitions",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: {
          mode: "interleaved",
          stride: 2,
          partitions: { victim: { share: 50 }, aggressor: { share: 50 } },
        },
      },
      ...baseExp,
    },
  },
  {
    name: "split number array (by app)",
    config: {
      global_options: {
        numnodes: "10",
        ppn: "1",
        allocation: { mode: "interleaved", split: [60, 40] },
      },
      ...baseExp,
    },
  },
  {
    name: "random + seed",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: {
          mode: "random",
          seed: 42,
          partitions: { victim: { share: 50 }, aggressor: { share: 50 } },
        },
      },
      ...baseExp,
    },
  },
  {
    name: "no allocation key emits none",
    config: { global_options: { numnodes: "8", ppn: "1" }, ...baseExp },
  },
  {
    name: "tunable options (convergence/output/advanced)",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        minruns: "5",
        maxruns: "30",
        timeout: "3600.0",
        convergeall: true,
        alpha: "0.05",
        beta: "0.1",
        outformat: "csv",
        retain_files: false,
        tags: "study",
        extrainfo: "noise_study",
        walltime: "00:20:00",
        datapath: "/scratch/d",
      },
      ...baseExp,
    },
  },
  {
    name: "convergeall:false / retain_files:true are explicit (not dropped)",
    config: {
      global_options: { numnodes: "8", ppn: "1", convergeall: false, retain_files: true },
      ...baseExp,
    },
  },
  {
    name: "partition with inner mode/split (passthrough)",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: {
          mode: "linear",
          partitions: { grp: { share: 100, mode: "interleaved", split: [50, 50] } },
        },
      },
      ...baseExp,
    },
  },
  {
    name: "local_options: bare-linear allocation override + minruns",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: {
          mode: "linear",
          partitions: { victim: { share: 50 }, aggressor: { share: 50 } },
        },
      },
      experiments: {
        ex1: {
          apps: { 0: { path: "a.py", args: "", collect: true, start: "0", end: "" } },
          local_options: { allocation: { mode: "linear" }, minruns: "5" },
        },
      },
    },
  },
  {
    name: "local_options: per-experiment allocation override (interleaved+stride+groups)",
    config: {
      global_options: { numnodes: "8", ppn: "1" },
      experiments: {
        ex1: {
          apps: {
            0: { path: "a.py", args: "", collect: true, start: "0", end: "", partition: "victim" },
          },
          local_options: {
            allocation: {
              mode: "interleaved",
              stride: 2,
              partitions: { victim: { share: 50 }, aggressor: { share: 50 } },
            },
            timeout: "3600.0",
          },
        },
      },
    },
  },
  {
    name: "sbatch_directives: dict form (time + job-name + exclusive:true)",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        sbatch_directives: { time: "00:30:00", "job-name": "crab_x", exclusive: true },
      },
      ...baseExp,
    },
  },
  {
    name: "sbatch_directives: list form",
    config: {
      global_options: {
        numnodes: "8",
        ppn: "1",
        sbatch_directives: ["--exclusive", "--time=00:20:00"],
      },
      ...baseExp,
    },
  },
  {
    name: "app extra keys (wrapper attrs) preserved + omitted collect means false",
    config: {
      global_options: { numnodes: "4" },
      experiments: {
        ex1: {
          apps: {
            0: { path: "a.py", args: "-x 1", start: "0", end: "", msgsize: 8192, warmup: true }, // no collect
            1: { path: "b.py", args: "", collect: true, start: "0", end: "", custom: "v" },
          },
        },
      },
    },
  },
  {
    // SbatchMan campaigns: an exact {name} token in a numeric
    // allocation field must survive as-is, not become NaN -> null.
    name: "allocation numerics: {var} placeholders in stride, seed, share",
    config: {
      global_options: {
        numnodes: "{nodes}",
        allocation: {
          mode: "interleaved",
          stride: "{stride}",
          partitions: { g1: { share: "{s1}" }, g2: { share: "{s2}" } },
        },
      },
      experiments: {
        ex1: {
          local_options: { allocation: { mode: "random", seed: "{seed}" } },
          apps: { 0: { path: "a.py", args: "", start: "0", end: "", partition: "g1" } },
        },
      },
    },
  },
  {
    name: "launcher in global options",
    config: {
      global_options: { numnodes: "2", launcher: "mpirun" },
      experiments: { ex1: { apps: { 0: { path: "a.py", args: "", start: "0", end: "" } } } },
    },
  },
  {
    name: "empty launcher_flags (no extra flags)",
    config: {
      global_options: { numnodes: "2", launcher_flags: [] },
      experiments: { ex1: { apps: { 0: { path: "a.py", args: "", start: "0", end: "" } } } },
    },
  },
  {
    name: "launcher_flags with one flag",
    config: {
      global_options: { numnodes: "2", launcher_flags: ["--cpu-bind=socket"] },
      experiments: { ex1: { apps: { 0: { path: "a.py", args: "", start: "0", end: "" } } } },
    },
  },
  {
    name: "launcher_flags with a flag that holds a space",
    config: {
      global_options: { numnodes: "2", launcher_flags: ["--bind-to core", "-v"] },
      experiments: { ex1: { apps: { 0: { path: "a.py", args: "", start: "0", end: "" } } } },
    },
  },
  {
    name: "launcher and launcher_flags in an experiment's local_options",
    config: {
      global_options: { numnodes: "2", launcher: "srun" },
      experiments: {
        ex1: {
          local_options: { launcher: "direct", launcher_flags: ["--exact", "-v"] },
          apps: { 0: { path: "a.py", args: "", start: "0", end: "" } },
        },
      },
    },
  },
  {
    name: "ppn per app and per experiment, next to the global one",
    config: {
      global_options: { numnodes: "2", ppn: "1" },
      experiments: {
        ex1: {
          local_options: { ppn: "2" },
          apps: {
            0: { path: "a.py", args: "", collect: true, start: "0", end: "", ppn: "4" },
            1: { path: "b.py", args: "", collect: false, start: "0", end: "f" },
          },
        },
      },
    },
  },
];

describe("config round-trip (synthetic fixtures)", () => {
  it.each(fixtures)("$name", ({ config }) => {
    const round = toConfig(fromConfig(config));
    // fromConfig normalizes split->partitions, so compare against the
    // normalized input (a no-op for configs already in partitions form).
    expect(project(round)).toEqual(project(normalizeSplitToPartitions(config)));
    // No allocation key may appear or vanish across the round-trip.
    expect("allocation" in (round.global_options || {})).toBe(
      "allocation" in (config.global_options || {}),
    );
  });
});

describe("split -> partitions normalization (ADR-007, semantic equivalence)", () => {
  it("split [50,50] with untagged apps normalizes to auto-named groups + tags", () => {
    const splitFormInput = {
      global_options: { numnodes: "8", ppn: "1", allocation: { mode: "linear", split: [50, 50] } },
      experiments: {
        ex1: {
          apps: {
            0: { path: "a.py", args: "", collect: true, start: "0", end: "" },
            1: { path: "b.py", args: "", collect: false, start: "0", end: "f" },
          },
        },
      },
    };
    const partitionsForm = {
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: { mode: "linear", partitions: { group_1: {}, group_2: {} } },
      },
      experiments: {
        ex1: {
          apps: {
            0: { path: "a.py", args: "", collect: true, start: "0", end: "", partition: "group_1" },
            1: {
              path: "b.py",
              args: "",
              collect: false,
              start: "0",
              end: "f",
              partition: "group_2",
            },
          },
        },
      },
    };
    const round = toConfig(fromConfig(splitFormInput));
    expect(semanticAlloc(round)).toEqual(semanticAlloc(partitionsForm));
  });

  it("mixed split [25,25,30,20]: share is all-or-nothing, so every group gets an explicit share", () => {
    // The engine rejects a mix of shared/unshared partitions; a non-even split
    // must never have its repeated values omitted as "even".
    const input = (apps: AnyObj) => ({
      global_options: {
        numnodes: "8",
        ppn: "1",
        allocation: { mode: "linear", split: [25, 25, 30, 20] },
      },
      experiments: { ex1: { apps } },
    });
    const normalized = normalizeSplitToPartitions(
      input({ 0: { path: "a.py", collect: true, start: "0", end: "" } }),
    );
    expect(
      Object.values(normalized.global_options.allocation.partitions).map((p: AnyObj) => p.share),
    ).toEqual([25, 25, 30, 20]);

    const round = toConfig(
      fromConfig(
        input({
          0: { path: "a.py", collect: true, start: "0", end: "" },
          1: { path: "b.py", collect: true, start: "0", end: "" },
          2: { path: "c.py", collect: true, start: "0", end: "" },
          3: { path: "d.py", collect: true, start: "0", end: "" },
        }),
      ),
    );
    expect(
      Object.values(round.global_options.allocation.partitions).map((p: AnyObj) => p.share),
    ).toEqual([25, 25, 30, 20]);
  });
});

describe("output format (csv only)", () => {
  it("does not re-emit hdf, which the engine refuses", () => {
    const d = fromConfig({ global_options: { numnodes: "2", outformat: "hdf" }, experiments: {} });
    expect("outformat" in toConfig(d).global_options).toBe(false);
  });
});

describe("config format version", () => {
  it("every emitted config carries schema_version 2, even from an untouched editor", () => {
    expect(toConfig(emptyDraft()).schema_version).toBe(2);
  });

  it("re-emits schema_version 2 for an imported config that had none", () => {
    const old = fromConfig({ global_options: { numnodes: "2" }, experiments: {} });
    expect(toConfig(old).schema_version).toBe(2);
  });
});

describe("validation and emit-on-set invariants (ADR-005)", () => {
  it("flags an app whose partition references no defined node group", () => {
    const orphan = fromConfig({
      global_options: { numnodes: "8", ppn: "1" },
      experiments: { ex1: { apps: { 0: { path: "a.py", partition: "victim", collect: true } } } },
    });
    const issues = validateDraft(orphan).filter((m: string) => /not a defined node group/.test(m));
    expect(issues).toHaveLength(1);
  });

  it("an untouched editor and a bare-linear import both emit no allocation key", () => {
    const untouched = emptyDraft();
    untouched.numnodes = "8";
    const ex = emptyExperiment("ex1");
    ex.apps.push(emptyApp());
    ex.apps[0].path = "a.py";
    untouched.experiments = [ex];

    const bareLinear = fromConfig({
      global_options: { numnodes: "8", allocation: { mode: "linear" } },
      experiments: { ex1: { apps: { 0: { path: "a.py" } } } },
    });

    expect("allocation" in (toConfig(untouched).global_options || {})).toBe(false);
    expect("allocation" in (toConfig(bareLinear).global_options || {})).toBe(false);
  });

  it("a bare-linear local override REPLACES the global groups (orphan flagged)", () => {
    const overridden = fromConfig({
      global_options: {
        numnodes: "8",
        allocation: { mode: "linear", partitions: { victim: {}, aggressor: {} } },
      },
      experiments: {
        ex1: {
          apps: { 0: { path: "a.py", partition: "victim", collect: true } },
          local_options: { allocation: { mode: "linear" } },
        },
      },
    });
    const issues = validateDraft(overridden).filter((m: string) =>
      /not a defined node group/.test(m),
    );
    expect(issues).toHaveLength(1);
  });

  it("an experiment with no overrides emits no local_options key", () => {
    const noOv = fromConfig({
      global_options: { numnodes: "8" },
      experiments: { ex1: { apps: { 0: { path: "a.py" } } } },
    });
    expect("local_options" in toConfig(noOv).experiments.ex1).toBe(false);
  });
});

describe("real example configs (handled-fields projection)", () => {
  const realFiles = [
    "examples/leonardo/congestion/layout_effect.json", // local_options.allocation (linear/interleaved/random + stride/seed)
    "examples/leonardo/congestion/co_scheduling.json", // split [50,50] + per-exp allocation overrides
    "examples/leonardo/congestion/noise_heatmap.json", // partitions + local_options.minruns
    "examples/leonardo/scale/comprehensive_64_nodes.json", // empty partitions {}
  ];
  it.each(realFiles.map((rel) => ({ rel })))("$rel", ({ rel }) => {
    const cfg = JSON.parse(readFileSync(`${REPO_ROOT}/${rel}`, "utf8"));
    const round = toConfig(fromConfig(cfg));
    expect(project(round)).toEqual(project(normalizeSplitToPartitions(cfg)));
  });
});

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

describe("normalizeSplitToPartitions with chained apps (ADR-032)", () => {
  // The engine gives a split share only to chain heads: an app with start "sN" runs on app N's
  // nodes (core/allocation/allocator.py allocate_experiment). Tagging it with a group of its own
  // would turn the chain into a handoff.
  const splitWithChain = (split: number[], local = false): AnyObj => {
    const apps = {
      0: { path: "a.py", collect: true, start: "0", end: "" },
      1: { path: "b.py", collect: true, start: "s0", end: "" },
      2: { path: "c.py", collect: true, start: "0", end: "" },
    };
    const allocation = { mode: "linear", split };
    return local
      ? {
          global_options: { numnodes: "4" },
          experiments: { ex1: { apps, local_options: { allocation } } },
        }
      : { global_options: { numnodes: "4", allocation }, experiments: { ex1: { apps } } };
  };

  it("tags only chain heads, in order, for a global split", () => {
    const n = normalizeSplitToPartitions(splitWithChain([75, 25]));
    expect(n.global_options.allocation.partitions).toEqual({
      group_1: { share: 75 },
      group_2: { share: 25 },
    });
    const parts = Object.values(n.experiments.ex1.apps).map((a: AnyObj) => a.partition);
    expect(parts).toEqual(["group_1", undefined, "group_2"]);
  });

  it("tags only chain heads for a per-experiment split", () => {
    const n = normalizeSplitToPartitions(splitWithChain([75, 25], true));
    const parts = Object.values(n.experiments.ex1.apps).map((a: AnyObj) => a.partition);
    expect(parts).toEqual(["group_1", undefined, "group_2"]);
  });

  it("round-trips a chained split config without giving the chained app a group", () => {
    const round = toConfig(fromConfig(splitWithChain([75, 25])));
    const parts = Object.values(round.experiments.ex1.apps).map((a: AnyObj) => a.partition);
    expect(parts).toEqual(["group_1", undefined, "group_2"]);
  });
});

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
