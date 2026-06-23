<script setup lang="ts">
// Live schematic of how the job's nodes divide among apps/groups. Display-only
// (reads nodeLayout); makes no claim about the engine's exact placement. Colors
// encode the node division (group / app slot); each app shows its own role pill.
import { computed } from "vue";
import { type AllocationDraft, type AppDraft, type FlowRole, nodeLayout } from "@/lib/config";

const props = defineProps<{
  apps: AppDraft[];
  alloc: AllocationDraft;
  numnodes: string;
}>();

const layout = computed(() => nodeLayout(props.apps, props.alloc, props.numnodes));
const track = computed(() => layout.value.blocks.filter((b) => b.division >= 0));
const orphan = computed(() => layout.value.blocks.find((b) => b.division < 0) || null);

// Division palette (wraps for >8 divisions). Strip cells + block accent share it.
const PALETTE = ["#6ea8fe", "#ff8c78", "#7ec699", "#b69cff", "#e0b352", "#56c2c2", "#e07db2", "#9aa0aa"];
function divColor(i: number): string {
  return i < 0 ? "#9aa0aa" : PALETTE[i % PALETTE.length];
}
function roleClass(role: FlowRole): string {
  return role === "aggressor" ? "role-aggr" : role === "timed" ? "role-timed" : "role-victim";
}

const modeNote = computed(() => {
  const m = props.alloc.mode;
  if (m === "interleaved") return "nodes dealt round-robin (interleaved)";
  if (m === "random") return "illustrative shuffle (the real order is randomized at runtime)";
  return "contiguous blocks (linear)";
});
const stripCapped = computed(
  () => layout.value.strip.total > layout.value.strip.shown && layout.value.strip.shown > 0,
);
</script>

<template>
  <div class="diag">
    <div class="dhead">
      <span class="dtitle">Layout preview</span>
      <span class="dmeta">{{ numnodes.trim() || "?" }} nodes · {{ alloc.mode }}</span>
    </div>

    <p v-if="!track.length" class="empty">Add an app, a split, or a node group to see the layout.</p>

    <div v-else class="track">
      <div v-for="b in track" :key="b.name + b.division" class="block"
           :style="{ flex: Math.max(b.weight, 8), borderLeftColor: divColor(b.division) }">
        <div class="btop">
          <span class="bname">{{ b.name }}</span>
          <span class="bcount">{{ b.approxNodes != null ? "~" + b.approxNodes : "? nodes" }}</span>
        </div>
        <p v-if="b.note" class="bnote">{{ b.note }}</p>
        <div class="applist">
          <div v-for="(ap, j) in b.apps" :key="j" class="app">
            <span class="aname">{{ ap.name }}</span>
            <span class="pill" :class="roleClass(ap.role)">{{ ap.role }}</span>
            <span v-if="ap.measured" class="meas">measured</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="layout.strip.cells.length" class="strip">
      <span v-for="(c, i) in layout.strip.cells" :key="i" class="cell"
            :style="{ background: divColor(c) }" />
    </div>
    <p v-if="track.length" class="cap">
      {{ modeNote }}<template v-if="stripCapped">. showing {{ layout.strip.shown }} of {{ layout.strip.total }} nodes</template>.
      Block widths and counts are approximate, not the engine's exact placement.
    </p>

    <div v-if="orphan" class="orphan">
      <span class="otag">Not placed</span>
      <span class="ohint">{{ orphan.note }}:</span>
      <span v-for="(ap, j) in orphan.apps" :key="j" class="oname">{{ ap.name }}</span>
    </div>
  </div>
</template>

<style scoped>
.diag { display: flex; flex-direction: column; gap: 0.5rem; }
.dhead { display: flex; justify-content: space-between; align-items: baseline; }
.dtitle { font-size: 0.82rem; font-weight: 600; color: var(--text); }
.dmeta { font-size: 0.72rem; color: var(--text3); font-family: var(--mono); }
.empty { color: var(--text3); font-size: 0.78rem; }
.track { display: flex; gap: 5px; overflow-x: auto; padding-bottom: 0.2rem; }
.block { border: 1px solid var(--border); border-left-width: 4px; border-radius: var(--r);
  background: var(--bg2); padding: 0.5rem 0.55rem; min-width: 120px; min-height: 60px;
  display: flex; flex-direction: column; gap: 0.25rem; }
.btop { display: flex; justify-content: space-between; align-items: baseline; gap: 0.4rem; }
.bname { font-size: 0.78rem; font-weight: 600; color: var(--text); }
.bcount { font-size: 0.66rem; color: var(--text3); font-family: var(--mono); white-space: nowrap; }
.bnote { font-size: 0.66rem; color: var(--warn, #e0b352); }
.applist { display: flex; flex-direction: column; gap: 0.2rem; }
.app { display: flex; align-items: center; gap: 0.32rem; flex-wrap: wrap; font-size: 0.72rem; color: var(--text2); }
.pill { font-size: 0.58rem; padding: 0.02rem 0.32rem; border-radius: 4px; border: 1px solid currentColor; }
.role-victim { color: #6ea8fe; } .role-aggr { color: #ff8c78; } .role-timed { color: #e0b352; }
.meas { font-size: 0.58rem; padding: 0.02rem 0.32rem; border-radius: 4px; background: var(--border); color: var(--text2); }
.strip { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 3px; margin-top: 0.2rem; }
.cell { height: 16px; border-radius: 3px; }
.cap { font-size: 0.68rem; color: var(--text3); line-height: 1.4; }
.orphan { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; font-size: 0.72rem; color: var(--text2); }
.otag { font-size: 0.58rem; padding: 0.02rem 0.32rem; border-radius: 4px; border: 1px solid var(--warn, #e0b352); color: var(--warn, #e0b352); }
.ohint { color: var(--text3); }
.oname { font-family: var(--mono); }
</style>
