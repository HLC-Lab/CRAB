<script setup lang="ts">
// SbatchMan campaign authoring: a rail with the campaign settings entry and the
// job groups, the selected group as three numbered cards (1 Group, 2 Variables,
// 3 Experiment: the CRAB experiment template via the now-decoupled
// ExperimentPane/AllocationEditor), a live YAML preview, and writing it to a
// connected cluster. Launching
// is SbatchMan's job (ADR-026): this view never triggers `sbatchman launch`.
import { computed, onMounted, ref, watchEffect } from "vue";
import { useSbatchmanStore } from "@/stores/sbatchman";
import { useRemotesStore } from "@/stores/remotes";
import ConfirmModal from "@/components/ConfirmModal.vue";
import CampaignLibraryBar from "@/components/sbatchman/CampaignLibraryBar.vue";
import CampaignBar from "@/components/sbatchman/CampaignBar.vue";
import GroupRail from "@/components/sbatchman/GroupRail.vue";
import GroupBasics from "@/components/sbatchman/GroupBasics.vue";
import SectionCard from "@/components/sbatchman/SectionCard.vue";
import VariablesCard from "@/components/sbatchman/VariablesCard.vue";
import CampaignPreview from "@/components/sbatchman/CampaignPreview.vue";
import WriteTarget from "@/components/sbatchman/WriteTarget.vue";
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

// One cluster choice for the whole page: the write target is also where the
// wrapper picker reads its catalog from.
const connectedClusters = computed(() =>
  remotes.items.filter((r) => r.connected).map((r) => r.name),
);
watchEffect(() => store.syncDestination(connectedClusters.value));

const showPreview = ref(true);
const preview = ref<InstanceType<typeof CampaignPreview> | null>(null);
// Works with the pane hidden: the pane opens itself before scrolling.
function showProblems(): void {
  preview.value?.revealProblems();
}

onMounted(() => {
  store.loadLibrary();
  remotes.refresh();
  if (store.groups.length) view.value = { kind: "group" };
});

function afterNew(): void {
  view.value = { kind: "campaign" };
}
function afterOpened(): void {
  view.value = store.groups.length ? { kind: "group" } : { kind: "campaign" };
}

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
    <div class="actionbar">
      <CampaignLibraryBar @new="afterNew" @opened="afterOpened" />
      <WriteTarget
        :connected-clusters="connectedClusters"
        :destination="store.destination"
        :staging-path="store.stagingPath"
        :busy="store.busy"
        :problem-count="store.issues.length"
        :can-write="store.canWrite"
        :last-write="store.lastWrite"
        @update:destination="store.destination = $event"
        @write="store.write"
        @show-problems="showProblems"
      />
    </div>

    <!-- Page-level so they show even with the YAML preview hidden. -->
    <p v-if="store.error" class="banner err" role="alert">{{ store.error }}</p>
    <p v-if="store.notice" class="banner info" role="status">{{ store.notice }}</p>

    <div class="layout">
      <GroupRail
        :groups="store.groups"
        :selected="view.kind === 'group' ? store.selected : -1"
        :campaign-active="view.kind === 'campaign'"
        :jobs-for-group="store.jobsForGroup"
        @select="selectGroup"
        @select-campaign="selectCampaign"
        @add="store.addGroup"
        @request-remove="requestRemoveGroup"
      />

      <main class="pane">
        <SectionCard
          v-if="view.kind === 'campaign'"
          title="Campaign settings"
          subtitle="Where the job environment comes from"
        >
          <CampaignBar />
        </SectionCard>

        <template v-else-if="group">
          <SectionCard
            :n="1"
            title="Group"
            subtitle="How SbatchMan names and submits this group's jobs"
          >
            <GroupBasics
              :tag="group.tag"
              :preset="group.preset"
              :draft="group.draft"
              :samples="store.tagSamples(store.selected)"
              @update:tag="group.tag = $event"
              @update:preset="group.preset = $event"
            />
          </SectionCard>
          <SectionCard
            :n="2"
            title="Variables"
            subtitle="Every combination of values becomes one job"
          >
            <VariablesCard
              :group-variables="group.variables"
              :campaign-variables="store.variables"
            />
          </SectionCard>
          <SectionCard
            v-if="experiment"
            :n="3"
            title="Experiment"
            subtitle="What runs inside each job"
          >
            <ExperimentPane
              :experiment="experiment"
              :exp-index="0"
              :global-allocation="group.draft.allocation"
              :global-numnodes="group.draft.numnodes"
              :source-cluster="store.destination"
              hide-remove
              hide-cluster-select
            />
          </SectionCard>
        </template>

        <p v-else class="empty pad">Add a group to start authoring the campaign.</p>

        <!-- With no group there is no Variables card: keep the campaign-wide ones reachable. -->
        <SectionCard v-if="!group" title="Variables" subtitle="Shared by every group you add">
          <VariablesCard :campaign-variables="store.variables" />
        </SectionCard>
      </main>

      <CampaignPreview
        ref="preview"
        v-model:open="showPreview"
        :yaml="store.yaml"
        :total-jobs="store.totalJobs"
        :issues="store.issues"
      />
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
/* The page scrolls inside itself (app header 48px + status bar above and below):
   App's content wrapper is itself an overflow container that never scrolls, so a
   sticky bar could not stick to the window. */
.sbatchman {
  padding: 0 1.5rem 1.25rem;
  max-width: 98rem;
  height: calc(100vh - 48px - 2.4rem);
  overflow: auto;
}
/* The write controls never scroll away. */
.actionbar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 1.25rem;
  background: var(--bg);
  /* extend over the page's side padding so scrolled content cannot peek out there */
  margin: 0 -1.5rem 0.25rem;
  padding: 1rem 1.5rem 0.75rem;
}
.banner {
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
  margin: 0 0 1rem;
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
}
.banner.info {
  background: var(--accent-glow);
  color: var(--text2);
  border: 1px solid var(--border2);
}
.layout {
  display: grid;
  grid-template-columns: 15rem minmax(45rem, 1fr) auto;
  gap: 1rem;
  align-items: start;
  min-width: 60rem;
}
.pane {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
.empty.pad {
  padding: 2rem;
}
</style>
