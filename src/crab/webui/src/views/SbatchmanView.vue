<script setup lang="ts">
// SbatchMan campaign authoring (plan 084 S6): campaign-level settings, a rail
// of job groups, the selected group's SbatchMan fields + its CRAB experiment
// template (via the now-decoupled ExperimentPane/AllocationEditor, S5), and a
// live YAML preview. Writing/launching the composed YAML is S7/S8.
import { computed, onMounted, ref, watchEffect } from "vue";
import { useSbatchmanStore } from "@/stores/sbatchman";
import { useRemotesStore } from "@/stores/remotes";
import ConfirmModal from "@/components/ConfirmModal.vue";
import CampaignBar from "@/components/sbatchman/CampaignBar.vue";
import GroupRail from "@/components/sbatchman/GroupRail.vue";
import GroupBasics from "@/components/sbatchman/GroupBasics.vue";
import CampaignPreview from "@/components/sbatchman/CampaignPreview.vue";
import ExperimentPane from "@/components/author/ExperimentPane.vue";

const store = useSbatchmanStore();
const remotes = useRemotesStore();

type View = { kind: "campaign" | "group" };
const view = ref<View>({ kind: "campaign" });
function selectCampaign() {
  view.value = { kind: "campaign" };
}
function selectGroup(i: number) {
  store.selected = i;
  view.value = { kind: "group" };
}

const group = computed(() => store.groups[store.selected]);
const experiment = computed(() => group.value?.draft.experiments[0]);

// Cluster source for ExperimentPane's wrapper picker (same pattern as AuthorView).
const connectedClusters = computed(() =>
  remotes.items.filter((r) => r.connected).map((r) => r.name),
);
const sourceCluster = ref("");
watchEffect(() => {
  if (!connectedClusters.value.includes(sourceCluster.value)) {
    sourceCluster.value = connectedClusters.value[0] ?? "";
  }
});

const showPreview = ref(true);

onMounted(() => {
  remotes.refresh();
  if (store.groups.length) view.value = { kind: "group" };
});

const removeGroupTarget = ref<number | null>(null);
function requestRemoveGroup(i: number): void {
  removeGroupTarget.value = i;
}
function confirmRemoveGroup(): void {
  if (removeGroupTarget.value !== null) store.removeGroup(removeGroupTarget.value);
  removeGroupTarget.value = null;
}
</script>

<template>
  <section class="sbatchman">
    <header class="toolbar">
      <button class="btn" :class="{ on: view.kind === 'campaign' }" @click="selectCampaign">
        Campaign settings
      </button>
      <span class="spacer" />
      <button class="btn" @click="showPreview = !showPreview">
        {{ showPreview ? "Hide" : "Show" }} YAML preview
      </button>
    </header>

    <div class="layout">
      <GroupRail
        :groups="store.groups"
        :selected="store.selected"
        :jobs-for-group="store.jobsForGroup"
        @select="selectGroup"
        @add="store.addGroup"
        @request-remove="requestRemoveGroup"
      />

      <main class="pane">
        <template v-if="view.kind === 'campaign'">
          <CampaignBar
            :configs-path="store.configsPath"
            :crab-root="store.crabRoot"
            :system="store.system"
            :env="store.env"
            :variables="store.variables"
            @update:configs-path="store.configsPath = $event"
            @update:crab-root="store.crabRoot = $event"
            @update:system="store.system = $event"
          />
        </template>

        <template v-else-if="group && experiment">
          <GroupBasics
            :tag="group.tag"
            :preset="group.preset"
            :variables="group.variables"
            :draft="group.draft"
            :samples="store.tagSamples(store.selected)"
            @update:tag="group.tag = $event"
            @update:preset="group.preset = $event"
          />
          <ExperimentPane
            :experiment="experiment"
            :exp-index="0"
            :global-allocation="group.draft.allocation"
            :global-numnodes="group.draft.numnodes"
            v-model:source-cluster="sourceCluster"
            hide-remove
          />
        </template>

        <p v-else class="empty pad">Add a group to start authoring the campaign.</p>
      </main>

      <CampaignPreview v-if="showPreview" :yaml="store.yaml" :total-jobs="store.totalJobs" />
    </div>

    <ConfirmModal
      v-if="removeGroupTarget !== null"
      title="Remove this group?"
      :message="`Remove ${store.groups[removeGroupTarget]?.tag ? '“' + store.groups[removeGroupTarget]!.tag + '”' : 'this group'}? This cannot be undone.`"
      confirm-label="Remove"
      @confirm="confirmRemoveGroup"
      @cancel="removeGroupTarget = null"
    />
  </section>
</template>

<style scoped>
.sbatchman {
  padding: 1.25rem 1.5rem;
  max-width: 98rem;
  overflow-x: auto;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1rem;
}
.spacer {
  flex: 1;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover {
  border-color: var(--accent);
}
.btn.on {
  border-color: var(--accent);
  color: var(--accent);
}

.layout {
  display: grid;
  grid-template-columns: 15rem minmax(45rem, 1fr) auto;
  gap: 1rem;
  align-items: start;
  min-width: 60rem;
}
.pane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 1.5rem;
  min-height: 18rem;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}
.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
.empty.pad {
  padding: 2rem;
}
</style>
