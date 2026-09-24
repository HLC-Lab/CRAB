// SbatchMan campaign state (plan 084 S6). One campaign = shared settings
// (SbatchMan configs.yaml reference, remote CRAB root, environment) plus a
// list of job groups. Each group owns its OWN small `Draft` (from lib/config,
// exactly one experiment inside) so ExperimentPane/AllocationEditor can be
// mounted independently per group with no shared state between them — no
// Pinia store per group is needed, a plain reactive object is enough.
import { defineStore } from "pinia";
import { computed, reactive, ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { CampaignEntry, SbatchmanWriteResult } from "@/api/types";
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
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export interface EnvPair {
  key: string;
  value: string;
}

export interface GroupState {
  tag: string;
  preset: string;
  variables: SbatchmanVar[];
  draft: Draft;
}

function emptyGroup(name: string): GroupState {
  const draft = emptyDraft();
  draft.experiments.push(emptyExperiment(name));
  return { tag: "", preset: "", variables: [], draft };
}

/** The saved/loaded shape of a campaign draft (plan 086) — everything needed to
 * reconstruct the editor's state, distinct from `SbatchmanCampaign` (the
 * composed/derived shape used to emit the YAML, where each group's `draft` has
 * already been reduced to a `CrabConfig` via `toConfig`). Persisted as an opaque
 * dict backend-side (`store/campaign_library.py`). */
export interface CampaignSpec {
  configsPath: string;
  crabRoot: string;
  system: string;
  env: EnvPair[];
  variables: SbatchmanVar[];
  groups: GroupState[];
}

// A saved spec is opaque to the backend and may predate fields added since
// (plan 090 S11g): fill every missing piece from the current empty shapes.
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
  const list = (v: unknown): any[] => (Array.isArray(v) ? v : []);
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  return {
    configsPath: str(s.configsPath),
    crabRoot: str(s.crabRoot),
    system: str(s.system),
    env: list(s.env).map((p) => ({ key: str(p?.key), value: str(p?.value) })),
    variables: list(s.variables).map((v) => ({ name: str(v?.name), values: list(v?.values) })),
    groups: list(s.groups).map((g) => ({
      tag: str(g?.tag),
      preset: str(g?.preset),
      variables: list(g?.variables).map((v) => ({ name: str(v?.name), values: list(v?.values) })),
      draft: normalizeDraft(g?.draft),
    })),
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export const useSbatchmanStore = defineStore("sbatchman", () => {
  const name = ref("campaign");
  const configsPath = ref("");
  const crabRoot = ref("");
  const system = ref("");
  const env = reactive<EnvPair[]>([{ key: "", value: "" }]);
  const variables = reactive<SbatchmanVar[]>([]);
  const groups = reactive<GroupState[]>([emptyGroup("run")]);
  const selected = ref(0);

  // Destination + write state (S8): which connected profile to push the
  // campaign to, and the outcome of the last write round-trip. Launching is
  // SbatchMan's job now (plan 085) — the store has no launch state.
  const destination = ref("");
  const busy = ref(false);
  const error = ref<string | null>(null);
  const lastWrite = ref<SbatchmanWriteResult | null>(null);

  // Campaign library (plan 086): save/load, mirroring stores/author.ts.
  const library = ref<CampaignEntry[]>([]);
  const entryId = ref<string | null>(null);
  const notice = ref<string | null>(null);

  const spec = computed<CampaignSpec>(() => ({
    configsPath: configsPath.value,
    crabRoot: crabRoot.value,
    system: system.value,
    env: env.map((p) => ({ ...p })),
    variables: variables.map((v) => ({ ...v })),
    groups: groups.map((g) => ({
      tag: g.tag,
      preset: g.preset,
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

  const envRecord = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const p of env) if (p.key.trim()) out[p.key.trim()] = p.value;
    return out;
  });

  const campaign = computed<SbatchmanCampaign>(() => ({
    configsPath: configsPath.value.trim(),
    crabRoot: crabRoot.value.trim(),
    system: system.value.trim(),
    env: envRecord.value,
    variables: variables.map((v) => ({ ...v })),
    groups: groups.map((g) => ({
      tag: g.tag,
      preset: g.preset,
      variables: g.variables.map((v) => ({ ...v })),
      config: toConfig(g.draft),
    })),
  }));

  const yaml = computed(() => composeCampaignYaml(campaign.value));
  const totalJobs = computed(() => campaignJobCount(campaign.value));
  // Problems that would break `sbatchman launch` or the CRAB worker; write() refuses
  // while any exist (plan 090 S11d).
  const issues = computed(() => validateCampaign(campaign.value));

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
    configsPath.value = s.configsPath;
    crabRoot.value = s.crabRoot;
    system.value = s.system;
    env.splice(0, env.length, ...s.env.map((p) => ({ ...p })));
    if (!env.length) env.push({ key: "", value: "" });
    variables.splice(0, variables.length, ...s.variables.map((v) => ({ ...v })));
    groups.splice(
      0,
      groups.length,
      ...s.groups.map((g) => ({
        tag: g.tag,
        preset: g.preset,
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
    _load({ configsPath: "", crabRoot: "", system: "", env: [], variables: [], groups: [] });
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
      await loadLibrary();
      savedSnapshot.value = savedStateJson.value;
      flashNotice(`Saved "${n}".`);
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

  async function write(): Promise<boolean> {
    error.value = null;
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
    try {
      lastWrite.value = await api.sbatchman.write(destination.value, yaml.value, name.value);
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    } finally {
      busy.value = false;
    }
  }

  return {
    name,
    configsPath,
    crabRoot,
    system,
    env,
    variables,
    groups,
    selected,
    destination,
    busy,
    error,
    lastWrite,
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
