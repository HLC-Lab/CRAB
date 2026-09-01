// SbatchMan campaign state (plan 084 S6). One campaign = shared settings
// (SbatchMan configs.yaml reference, remote CRAB root, environment) plus a
// list of job groups. Each group owns its OWN small `Draft` (from lib/config,
// exactly one experiment inside) so ExperimentPane/AllocationEditor can be
// mounted independently per group with no shared state between them — no
// Pinia store per group is needed, a plain reactive object is enough.
import { defineStore } from "pinia";
import { computed, reactive, ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { SbatchmanLaunchResult, SbatchmanWriteResult } from "@/api/types";
import { type Draft, emptyDraft, emptyExperiment, toConfig } from "@/lib/config";
import {
  campaignJobCount,
  composeCampaignYaml,
  groupJobCount,
  sampleTags,
  type SbatchmanCampaign,
  type SbatchmanVar,
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

export const useSbatchmanStore = defineStore("sbatchman", () => {
  const name = ref("campaign");
  const configsPath = ref("");
  const crabRoot = ref("");
  const system = ref("");
  const env = reactive<EnvPair[]>([{ key: "", value: "" }]);
  const variables = reactive<SbatchmanVar[]>([]);
  const groups = reactive<GroupState[]>([emptyGroup("run")]);
  const selected = ref(0);

  // Destination + write/launch state (S8): which connected profile to push
  // the campaign to, and the outcome of the last write/launch round-trip.
  const destination = ref("");
  const busy = ref(false);
  const error = ref<string | null>(null);
  const lastWrite = ref<SbatchmanWriteResult | null>(null);
  const lastLaunch = ref<SbatchmanLaunchResult | null>(null);

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

  async function write(): Promise<boolean> {
    error.value = null;
    lastLaunch.value = null;
    if (!destination.value) {
      error.value = "Choose a connected cluster to write to first.";
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

  async function launch(): Promise<boolean> {
    error.value = null;
    if (!destination.value || !lastWrite.value) {
      error.value = "Write the campaign to the cluster before launching it.";
      return false;
    }
    busy.value = true;
    try {
      lastLaunch.value = await api.sbatchman.launch(destination.value, lastWrite.value.remote_path);
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
    lastLaunch,
    addGroup,
    removeGroup,
    campaign,
    yaml,
    totalJobs,
    jobsForGroup,
    tagSamples,
    write,
    launch,
  };
});
