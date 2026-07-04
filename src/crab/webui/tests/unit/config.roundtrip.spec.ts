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
  emptyApp,
  emptyDraft,
  emptyExperiment,
  fromAllocation,
  fromConfig,
  normalizeSplitToPartitions,
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
];
const GLOBAL_KEYS = new Set([
  "name",
  "numnodes",
  "ppn",
  "allocation",
  "sbatch_directives",
  ...OPTION_KEYS,
]);
// numnodes/ppn/name are job-level only and never valid in local_options.
const LOCAL_KEYS = new Set(["allocation", ...OPTION_KEYS]);

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

const fixtures: { name: string; config: AnyObj }[] = [
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
