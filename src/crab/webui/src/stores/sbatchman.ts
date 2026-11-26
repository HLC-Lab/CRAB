// SbatchMan campaign state. One campaign = shared sweep variables plus a list of
// job groups. Each group owns its OWN small `Draft` (from lib/config,
// exactly one experiment inside) so ExperimentPane/AllocationEditor can be
// mounted independently per group with no shared state between them — no
// Pinia store per group is needed, a plain reactive object is enough.
import { defineStore } from "pinia";
import { computed, reactive, ref, watch } from "vue";
import { api, ApiError } from "@/api/client";
import { useCatalogStore } from "@/stores/catalog";
import type { CampaignEntry, SbatchmanTarget, SbatchmanWriteResult } from "@/api/types";
import {
  type Draft,
  emptyAllocation,
  emptyApp,
  emptyDraft,
  emptyExperiment,
  emptyOptions,
  emptySbatch,
  toConfig,
} from "@/lib/config";
import {
  campaignJobCount,
  composeCampaignYaml,
  groupJobCount,
  sampleTags,
  type SbatchmanCampaign,
  type SbatchmanVar,
  validateCampaign,
} from "@/lib/sbatchman";

function msg(e: unknown): string {
  if (e instanceof ApiError || e instanceof CampaignVersionError) return e.message;
  return "Unexpected error";
}

/** Format of a saved campaign spec. A spec saved before the field existed is version 1. */
export const CAMPAIGN_SPEC_VERSION = 1;

/** How long the path preview waits for typing to pause before asking the backend. */
export const STAGING_DEBOUNCE_MS = 300;

/** The backend's rule for a benchmark folder (`remoteops/umbrella.py`): one safe name. */
const BENCHMARK_FOLDER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/;

/** A saved campaign in a format newer than this dashboard understands. */
export class CampaignVersionError extends Error {}

export interface GroupState {
  tag: string;
  configName: string;
  variables: SbatchmanVar[];
  draft: Draft;
}

function emptyGroup(name: string): GroupState {
  const draft = emptyDraft();
  draft.experiments.push(emptyExperiment(name));
  return { tag: "", configName: "", variables: [], draft };
}

/** The saved/loaded shape of a campaign draft: everything needed to
 * reconstruct the editor's state, distinct from `SbatchmanCampaign` (the
 * composed/derived shape used to emit the YAML, where each group's `draft` has
 * already been reduced to a `CrabConfig` via `toConfig`). Persisted as an opaque
 * dict backend-side (`store/campaign_library.py`). */
export interface CampaignSpec {
  version: number;
  /** The `configs:` value; missing in a spec saved before the field existed. */
  configsFile?: string;
  /** The benchmark folder the user typed; empty means the default. Missing in an older spec. */
  benchmark?: string;
  variables: SbatchmanVar[];
  groups: GroupState[];
}

// A saved spec is opaque to the backend and may predate fields added later:
// fill every missing piece from the current empty shapes. Keys this dashboard no
// longer knows (the old crabRoot, system and env) are dropped silently.
/* eslint-disable @typescript-eslint/no-explicit-any -- walking untyped saved JSON */
function normalizeDraft(raw: any): Draft {
  const d = raw && typeof raw === "object" ? raw : {};
  const experiments = Array.isArray(d.experiments) ? d.experiments : [];
  const draft: Draft = {
    ...emptyDraft(),
    ...d,
    allocation: { ...emptyAllocation(), ...d.allocation },
    options: { ...emptyOptions(), ...d.options },
    sbatch: { ...emptySbatch(), ...d.sbatch },
    experiments: experiments.map((e: any) => ({
      ...emptyExperiment(),
      ...e,
      allocation: { ...emptyAllocation(), ...e?.allocation },
      options: { ...emptyOptions(), ...e?.options },
      apps: (Array.isArray(e?.apps) ? e.apps : []).map((a: any) => ({ ...emptyApp(), ...a })),
    })),
  };
  if (!draft.experiments.length) draft.experiments.push(emptyExperiment("run"));
  return draft;
}

function normalizeSpec(raw: unknown): CampaignSpec {
  const s = (raw && typeof raw === "object" ? raw : {}) as any;
  const saved = s.version ?? 1;
  if (!Number.isInteger(saved) || saved > CAMPAIGN_SPEC_VERSION) {
    throw new CampaignVersionError(
      `This campaign was saved by a newer dashboard (format ${saved}). Run crab update on this laptop to open it.`,
    );
  }
  const list = (v: unknown): any[] => (Array.isArray(v) ? v : []);
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  return {
    version: CAMPAIGN_SPEC_VERSION,
    configsFile: str(s.configsFile),
    benchmark: str(s.benchmark),
    variables: list(s.variables).map((v) => ({ name: str(v?.name), values: list(v?.values) })),
    groups: list(s.groups).map((g) => ({
      tag: str(g?.tag),
      configName: str(g?.configName),
      variables: list(g?.variables).map((v) => ({ name: str(v?.name), values: list(v?.values) })),
      draft: normalizeDraft(g?.draft),
    })),
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

/** What one write sent: the cluster, the campaign name and the composed YAML. */
interface WriteSnapshot {
  destination: string;
  name: string;
  yaml: string;
  benchmark: string | undefined;
}

export const useSbatchmanStore = defineStore("sbatchman", () => {
  const name = ref("campaign");
  const variables = reactive<SbatchmanVar[]>([]);
  const groups = reactive<GroupState[]>([emptyGroup("run")]);
  const selected = ref(0);
  // The `configs:` value of the jobs file (empty = no key).
  const configsFile = ref("");

  // Destination + write state: which connected profile to push the
  // campaign to, and the outcome of the last write round-trip. Launching is
  // SbatchMan's job now (ADR-026): the store has no launch state.
  const destination = ref("");
  const busy = ref(false);
  const error = ref<string | null>(null);
  const lastWrite = ref<SbatchmanWriteResult | null>(null);
  // The benchmark folder that write went into (empty on a plain install): the folder field can
  // change afterwards, the next-step command must still name where the file is.
  const lastWriteBenchmark = ref("");
  // The write a 409 answered ("the cluster already has this file"). The view asks whether
  // to replace it; write({ overwrite: true }) re-sends only while the cluster, name and
  // YAML are still the ones that write had.
  const overwriteFor = ref<WriteSnapshot | null>(null);
  // Where Write files will put the YAML on `destination`, computed by the
  // backend (it owns the slug and staging-dir rules). Empty until known.
  const stagingPath = ref("");

  // What the chosen cluster looks like (an umbrella install or a plain one), once known. A
  // failed lookup leaves it null, which behaves as a plain install: authoring is never blocked.
  const target = ref<SbatchmanTarget | null>(null);
  // The benchmark folder as typed. Empty in an umbrella means the first existing folder.
  const benchmark = ref("");
  const isUmbrella = computed(() => target.value?.kind === "umbrella");
  const effectiveBenchmark = computed(() => {
    const t = target.value;
    if (!t || t.kind !== "umbrella") return "";
    return benchmark.value.trim() || t.benchmarks[0] || "";
  });
  // What goes to the backend: the folder in an umbrella, otherwise nothing (never "").
  const sentBenchmark = computed<string | undefined>(
    () => (isUmbrella.value && effectiveBenchmark.value) || undefined,
  );
  const benchmarkProblem = computed<string | null>(() => {
    if (!isUmbrella.value) return null;
    const folder = effectiveBenchmark.value;
    if (!folder) return "Name the benchmark folder this campaign goes into.";
    if (!BENCHMARK_FOLDER.test(folder))
      return "The benchmark folder must be one name of letters, digits, dots, dashes and underscores, at most 60 characters, starting with a letter or digit.";
    return null;
  });
  const configNames = computed(() => (isUmbrella.value ? (target.value?.configs_names ?? []) : []));
  // The `configs:` value: what the user typed, else the umbrella's own configs file as the
  // benchmark folder sees it (`benchmarks/<b>/` is two levels below the install directory).
  const configsRef = computed(() => {
    const typed = configsFile.value.trim();
    if (typed) return typed;
    const file = target.value?.kind === "umbrella" ? target.value.configs_file : "";
    return file ? `../../.local/${file.split("/").pop()}` : "";
  });

  // Campaign library: save/load, mirroring stores/author.ts.
  const library = ref<CampaignEntry[]>([]);
  const entryId = ref<string | null>(null);
  const notice = ref<string | null>(null);

  const spec = computed<CampaignSpec>(() => ({
    version: CAMPAIGN_SPEC_VERSION,
    configsFile: configsFile.value,
    benchmark: benchmark.value,
    variables: variables.map((v) => ({ ...v })),
    groups: groups.map((g) => ({
      tag: g.tag,
      configName: g.configName,
      variables: g.variables.map((v) => ({ ...v })),
      draft: g.draft,
    })),
  }));
  // `name` lives outside `spec` (it's the library entry's own field, not part
  // of the persisted campaign spec), but a rename with nothing else touched is
  // still an unsaved change — so the dirty-check snapshot covers both.
  const savedStateJson = computed(() => JSON.stringify({ name: name.value, spec: spec.value }));

  // Snapshot of `savedStateJson` as of the last save/open/new-campaign.
  // `isDirty` only warns New/Browse when something would actually be lost
  // (mirrors stores/author.ts's `isDirty`). Seeded with the initial state so a
  // fresh, untouched campaign doesn't read as dirty.
  const savedSnapshot = ref<string>(savedStateJson.value);
  const isDirty = computed(() => savedStateJson.value !== savedSnapshot.value);

  function addGroup() {
    groups.push(emptyGroup("run"));
    selected.value = groups.length - 1;
  }
  function removeGroup(i: number) {
    groups.splice(i, 1);
    if (!groups.length) groups.push(emptyGroup("run"));
    selected.value = Math.min(selected.value, groups.length - 1);
  }

  const campaign = computed<SbatchmanCampaign>(() => ({
    configsFile: configsRef.value,
    variables: variables.map((v) => ({ ...v })),
    groups: groups.map((g) => ({
      tag: g.tag,
      configName: g.configName,
      variables: g.variables.map((v) => ({ ...v })),
      config: toConfig(g.draft),
    })),
  }));

  // The write target's wrapper catalog, once loaded: the same set the Author view checks
  // against. Without a chosen cluster or a loaded list there is nothing to check.
  const catalog = useCatalogStore();
  const wrapperCatalog = computed(() => {
    const cluster = destination.value;
    const list = cluster ? catalog.benchmarks[cluster]?.wrappers : undefined;
    if (!list) return undefined;
    return { cluster, wrappers: new Set(list.map((w) => w.relpath)) };
  });
  // Load it only when there is a wrapper path to check. The catalog store caches it, so this
  // is one request per cluster.
  const anyWrapperPath = computed(() =>
    groups.some((g) => g.draft.experiments.some((e) => e.apps.some((a) => a.path.trim()))),
  );
  watch(
    [anyWrapperPath, destination],
    () => {
      const cluster = destination.value;
      if (anyWrapperPath.value && cluster && !catalog.busy[cluster])
        catalog.loadBenchmarks(cluster);
    },
    { immediate: true },
  );

  const yaml = computed(() => composeCampaignYaml(campaign.value));
  const currentWrite = (): WriteSnapshot => ({
    destination: destination.value,
    name: name.value,
    yaml: yaml.value,
    benchmark: sentBenchmark.value,
  });
  const isCurrentWrite = (w: WriteSnapshot | null): boolean =>
    w !== null &&
    w.destination === destination.value &&
    w.name === name.value &&
    w.yaml === yaml.value &&
    w.benchmark === sentBenchmark.value;
  const needsOverwrite = computed(() => isCurrentWrite(overwriteFor.value));
  const totalJobs = computed(() => campaignJobCount(campaign.value));
  // Problems that would break `sbatchman launch` or the CRAB worker; write() refuses
  // while any exist.
  const issues = computed(() => validateCampaign(campaign.value, wrapperCatalog.value));

  // Why Write files is unavailable, if it is. Problems come first: the user can
  // act on them even with a cluster connected.
  type WriteBlock =
    | { kind: "issues"; count: number }
    | { kind: "no-cluster" }
    | { kind: "benchmark"; message: string };
  const writeBlock = computed<WriteBlock | null>(() => {
    if (issues.value.length) return { kind: "issues", count: issues.value.length };
    if (!destination.value) return { kind: "no-cluster" };
    if (benchmarkProblem.value) return { kind: "benchmark", message: benchmarkProblem.value };
    return null;
  });
  const canWrite = computed(() => writeBlock.value === null && !busy.value);

  /** Keep the shared cluster choice on a connected cluster (the first one by default). */
  function syncDestination(connected: string[]): void {
    if (!connected.includes(destination.value)) destination.value = connected[0] ?? "";
  }

  // The cluster's target, newest lookup wins. A failed lookup reads as a plain install.
  let targetRequest = 0;
  async function refreshTarget(): Promise<void> {
    const cluster = destination.value;
    const mine = ++targetRequest;
    if (!cluster) {
      target.value = null;
      return;
    }
    try {
      const found = await api.sbatchman.target(cluster);
      if (mine === targetRequest) target.value = found;
    } catch {
      if (mine === targetRequest) target.value = null;
    }
  }
  watch(destination, () => {
    target.value = null;
    void refreshTarget();
  });

  // The path preview: names change per keystroke, so it waits for typing to pause
  // (STAGING_DEBOUNCE_MS), runs one request at a time, and takes only an answer that still
  // describes the current cluster, name and folder. It is a convenience: a failed lookup shows
  // no path, and Write files reports its own errors.
  let stagingTimer: ReturnType<typeof setTimeout> | undefined;
  let stagingBusy = false;
  let stagingAgain = false;
  async function refreshStagingPath(): Promise<void> {
    if (stagingBusy) {
      stagingAgain = true;
      return;
    }
    stagingBusy = true;
    try {
      do {
        stagingAgain = false;
        const cluster = destination.value;
        const campaignName = name.value;
        const folder = sentBenchmark.value;
        if (!cluster || benchmarkProblem.value) {
          stagingPath.value = "";
          continue;
        }
        const current = () =>
          cluster === destination.value &&
          campaignName === name.value &&
          folder === sentBenchmark.value;
        try {
          const res = await api.sbatchman.stagingPath(cluster, campaignName, folder);
          if (current()) stagingPath.value = res.path;
        } catch {
          if (current()) stagingPath.value = "";
        }
      } while (stagingAgain);
    } finally {
      stagingBusy = false;
    }
  }
  watch([destination, name, sentBenchmark, benchmarkProblem], () => {
    clearTimeout(stagingTimer);
    if (!destination.value || benchmarkProblem.value) {
      stagingPath.value = "";
      return;
    }
    stagingTimer = setTimeout(() => void refreshStagingPath(), STAGING_DEBOUNCE_MS);
  });

  // "Written to ..." and the replace question describe the YAML as it was when written:
  // once the campaign or its name changes they would be stale.
  watch([yaml, name, destination, sentBenchmark], () => {
    lastWrite.value = null;
    overwriteFor.value = null;
  });

  function jobsForGroup(i: number): number {
    const c = campaign.value;
    const g = c.groups[i];
    return g ? groupJobCount(c, g) : 0;
  }
  function tagSamples(i: number): string[] {
    const c = campaign.value;
    const g = c.groups[i];
    return g ? sampleTags(c, g) : [];
  }

  function _load(s: CampaignSpec) {
    configsFile.value = s.configsFile ?? "";
    benchmark.value = s.benchmark ?? "";
    variables.splice(0, variables.length, ...s.variables.map((v) => ({ ...v })));
    groups.splice(
      0,
      groups.length,
      ...s.groups.map((g) => ({
        tag: g.tag,
        configName: g.configName,
        variables: g.variables.map((v) => ({ ...v })),
        draft: g.draft,
      })),
    );
    if (!groups.length) groups.push(emptyGroup("run"));
    selected.value = 0;
    savedSnapshot.value = savedStateJson.value;
  }

  async function loadLibrary(): Promise<void> {
    try {
      library.value = await api.sbatchman.campaigns.list();
    } catch (e) {
      error.value = msg(e);
    }
  }

  function newCampaign(): void {
    entryId.value = null;
    name.value = "campaign";
    _load({
      version: CAMPAIGN_SPEC_VERSION,
      variables: [],
      groups: [],
    });
    lastWrite.value = null;
    error.value = null;
    notice.value = null;
  }

  async function open(id: string): Promise<void> {
    error.value = null;
    notice.value = null;
    busy.value = true;
    try {
      const entry = await api.sbatchman.campaigns.get(id);
      entryId.value = entry.id;
      name.value = entry.name;
      lastWrite.value = null;
      // `entry.spec` is opaque to the backend; this store owns the shape.
      _load(normalizeSpec(entry.spec));
    } catch (e) {
      error.value = msg(e);
    } finally {
      busy.value = false;
    }
  }

  function flashNotice(text: string, ms = 3000): void {
    notice.value = text;
    setTimeout(() => {
      if (notice.value === text) notice.value = null;
    }, ms);
  }

  async function save(): Promise<boolean> {
    error.value = null;
    if (!name.value.trim()) {
      error.value = "Name the campaign before saving.";
      return false;
    }
    busy.value = true;
    try {
      const n = name.value.trim();
      const asDict = spec.value as unknown as Record<string, unknown>;
      const entry = entryId.value
        ? await api.sbatchman.campaigns.update(entryId.value, n, asDict)
        : await api.sbatchman.campaigns.create(n, asDict);
      entryId.value = entry.id;
      // The library keeps names unique: a taken name comes back as "<name>-2". The editor
      // takes the final name so it, the library entry and a later update agree.
      name.value = entry.name;
      await loadLibrary();
      savedSnapshot.value = savedStateJson.value;
      if (entry.name !== n) {
        flashNotice(`Saved as "${entry.name}" because the name "${n}" was taken.`, 6000);
      } else {
        flashNotice(`Saved "${n}".`);
      }
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    } finally {
      busy.value = false;
    }
  }

  async function duplicateCampaign(id: string): Promise<void> {
    try {
      const entry = await api.sbatchman.campaigns.duplicate(id);
      await loadLibrary();
      await open(entry.id); // clears `notice` as part of opening; flash after
      flashNotice(`Duplicated as "${entry.name}".`);
    } catch (e) {
      error.value = msg(e);
    }
  }

  async function removeCampaign(id: string): Promise<void> {
    try {
      await api.sbatchman.campaigns.remove(id);
      if (entryId.value === id) newCampaign();
      await loadLibrary();
    } catch (e) {
      error.value = msg(e);
    }
  }

  async function write(options: { overwrite?: boolean } = {}): Promise<boolean> {
    error.value = null;
    // Replace only what the 409 was about; anything else is a plain write again.
    const overwrite = options.overwrite === true && needsOverwrite.value;
    overwriteFor.value = null;
    if (!destination.value) {
      error.value = "Choose a connected cluster to write to first.";
      return false;
    }
    if (issues.value.length) {
      const n = issues.value.length;
      error.value = `Fix the ${n} issue${n === 1 ? "" : "s"} listed in the preview before writing.`;
      return false;
    }
    busy.value = true;
    // The campaign can change while the request is in flight: an answer for a different
    // cluster, name or YAML is dropped, not shown as if it described the current one.
    const sent = currentWrite();
    try {
      const written = await api.sbatchman.write(
        sent.destination,
        sent.yaml,
        sent.name,
        overwrite,
        sent.benchmark,
      );
      if (!isCurrentWrite(sent)) return false;
      lastWrite.value = written;
      lastWriteBenchmark.value = sent.benchmark ?? "";
      // A folder this write made is now one of the cluster's folders.
      if (written.new_folder) void refreshTarget();
      return true;
    } catch (e) {
      if (!isCurrentWrite(sent)) return false;
      if (e instanceof ApiError && e.code === "conflict") {
        overwriteFor.value = sent;
      } else {
        error.value = msg(e);
      }
      return false;
    } finally {
      busy.value = false;
    }
  }

  // What to do after a write, shown under the "Written" line. The dashboard never launches jobs
  // and never runs `sbatchman init` (ADR-026), so it says so and names the next commands. Commands
  // sit between backticks (the view renders them as code) and carry the install directory, so
  // they paste as they are. In an umbrella the order is: install.sh (a folder this write made has
  // no SbatchMan project yet), then the dry run SbatchMan offers, then the real launch.
  const nextStepNotice = computed<string | null>(() => {
    const written = lastWrite.value;
    if (!written) return null;
    const lead = "Not launched yet.";
    if (!isUmbrella.value) return `${lead} Launch it with SbatchMan on the cluster.`;
    const file = written.remote_path.slice(written.remote_path.lastIndexOf("/") + 1);
    const root = target.value?.root ?? "";
    const dryRun = `\`cd ${root}/benchmarks/${lastWriteBenchmark.value} && sbatchman launch -f ${file} --dry-run\``;
    if (written.new_folder) {
      return `${lead} New folder: run \`cd ${root} && ./install.sh\` to create its SbatchMan project and check the file. Then check what SbatchMan would submit with ${dryRun}. Drop \`--dry-run\` to launch.`;
    }
    return `${lead} To check what SbatchMan would submit, run ${dryRun}. Drop \`--dry-run\` to launch.`;
  });

  return {
    name,
    configsFile,
    configsRef,
    configNames,
    benchmark,
    effectiveBenchmark,
    benchmarkProblem,
    target,
    nextStepNotice,
    variables,
    groups,
    selected,
    destination,
    busy,
    error,
    lastWrite,
    needsOverwrite,
    stagingPath,
    writeBlock,
    canWrite,
    syncDestination,
    addGroup,
    removeGroup,
    campaign,
    yaml,
    totalJobs,
    issues,
    jobsForGroup,
    tagSamples,
    write,
    library,
    entryId,
    notice,
    isDirty,
    loadLibrary,
    newCampaign,
    open,
    save,
    duplicateCampaign,
    removeCampaign,
  };
});
