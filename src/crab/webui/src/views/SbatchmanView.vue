<script setup lang="ts">
// SbatchMan campaign authoring: a rail with the campaign settings entry and the
// job groups, the selected group as three numbered cards (1 Group, 2 Variables,
// 3 Experiment: the CRAB experiment template via the now-decoupled
// ExperimentPane/AllocationEditor), a live YAML preview, and writing it to a
// connected cluster. Launching
// is SbatchMan's job (ADR-026): this view never triggers `sbatchman launch`.
import { computed, nextTick, onMounted, ref, watch, watchEffect } from "vue";
import { campaignFileName } from "@/lib/sbatchman";
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

// While the bar shows the result of a write (or asks to replace a file) it is several lines
// tall: it scrolls with the page instead of sticking, and is brought into view so the result
// is seen even when the page was scrolled down. The first edit clears the notice (the store
// resets it) and the bar sticks again.
const actionbar = ref<HTMLElement | null>(null);
const hasNotice = computed(() => store.lastWrite !== null || store.needsOverwrite);
watch(hasNotice, async (on) => {
  if (!on) return;
  await nextTick();
  actionbar.value?.scrollIntoView({ block: "start", behavior: "smooth" });
});

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
    <div ref="actionbar" class="actionbar" :class="{ loose: hasNotice }">
      <CampaignLibraryBar @new="afterNew" @opened="afterOpened" />
      <WriteTarget
        :connected-clusters="connectedClusters"
        :destination="store.destination"
        :staging-path="store.stagingPath"
        :busy="store.busy"
        :problem-count="store.issues.length"
        :issues="store.issues"
        :can-write="store.canWrite"
        :is-umbrella="store.target?.kind === 'umbrella'"
        :benchmark="store.benchmark"
        :default-benchmark="store.effectiveBenchmark"
        :benchmarks="store.target?.benchmarks ?? []"
        :benchmark-problem="store.benchmarkProblem"
        :last-write="store.lastWrite"
        :next-step="store.nextStepNotice ?? ''"
        :needs-overwrite="store.needsOverwrite"
        @update:destination="store.destination = $event"
        @update:benchmark="store.benchmark = $event"
        @write="store.write"
        @replace="store.write({ overwrite: true })"
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

      <section class="pane">
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
              :config-name="group.configName"
              :config-names="store.configNames"
              :draft="group.draft"
              :samples="store.tagSamples(store.selected)"
              @update:tag="group.tag = $event"
              @update:configName="group.configName = $event"
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
      </section>

      <CampaignPreview
        v-model:open="showPreview"
        :yaml="store.yaml"
        :file-name="campaignFileName(store.name)"
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
/* The write controls never scroll away, except while they show a write result (see hasNotice). */
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
.actionbar.loose {
  position: static;
}
/* The library buttons keep their width; the write block (with its long path) takes what is left. */
.actionbar > :first-child {
  flex: none;
}
/* A narrow window: the bar stops being sticky and wraps, so the write block gets its own row
   instead of a sliver beside the library buttons. */
@media (max-width: 900px) {
  .actionbar {
    position: static;
    flex-wrap: wrap;
  }
  .actionbar > :deep(.write-target) {
    flex: 1 1 100%;
    margin-left: 0;
  }
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
/* Rail and pane side by side, the YAML preview under the pane. It moves to a third column
   only when the window has room for all three (15rem + 45rem + 26rem + gaps + page padding),
   so no window of 1280 px or more scrolls sideways. */
.layout {
  display: grid;
  grid-template-columns: 15rem minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
  min-width: 60rem;
}
.layout > :deep(.yamlpane) {
  grid-column: 2;
  width: auto;
}
@media (min-width: 1500px) {
  .layout {
    grid-template-columns: 15rem minmax(45rem, 1fr) auto;
  }
  .layout > :deep(.yamlpane) {
    grid-column: auto;
  }
  .layout > :deep(.yamlpane:not(.closed)) {
    width: 26rem;
  }
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
