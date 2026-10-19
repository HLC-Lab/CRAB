<script setup lang="ts">
// Body of the Variables card: the selected group's variables and the
// campaign-wide ones, as two scoped blocks over the same row editor. Without
// a group (`groupVariables` left out) only the campaign-wide block shows, so
// those variables stay reachable.
import type { SbatchmanVar } from "@/lib/sbatchman";
import VariablesEditor from "@/components/sbatchman/VariablesEditor.vue";

defineProps<{
  groupVariables?: SbatchmanVar[];
  campaignVariables: SbatchmanVar[];
}>();
</script>

<template>
  <div v-if="groupVariables" class="scope" role="group" aria-label="This group variables">
    <div class="scope-head">
      <span class="pill grp">This group</span>
      <span class="hint">A name here overrides a campaign-wide one.</span>
    </div>
    <VariablesEditor :variables="groupVariables" />
  </div>
  <div class="scope" role="group" aria-label="Campaign-wide variables">
    <div class="scope-head">
      <span class="pill">Campaign-wide</span>
      <span class="hint">Shared by every group unless it defines the same name.</span>
    </div>
    <VariablesEditor :variables="campaignVariables" />
  </div>
</template>

<style scoped>
.scope {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.scope + .scope {
  border-top: 1px dashed var(--border);
  padding-top: 0.9rem;
}
.scope-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.pill {
  font-family: var(--sans);
  font-size: var(--t-xs);
  border: 1px solid var(--border2);
  color: var(--text2);
  border-radius: 999px;
  padding: 0.05rem 0.55rem;
  white-space: nowrap;
}
.pill.grp {
  border-color: var(--accent);
  color: var(--accent);
}
.hint {
  color: var(--text3);
  font-size: var(--t-sm);
}
</style>
