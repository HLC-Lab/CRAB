<script setup lang="ts">
// One app in the flow diagram plus the apps that start after it, drawn as a
// dependency tree: the node on the left, an arrow per dependent, each dependent
// rendered recursively to the right. Roots (apps that start at run start or
// after a delay) are stacked vertically by the parent (they run together).
import { type FlowTree, flowRoleHint } from "@/lib/config";

defineProps<{ node: FlowTree }>();
</script>

<template>
  <div class="chain">
    <div class="node" :class="node.role" :title="flowRoleHint(node.role)">
      <span class="node-name">{{ node.name }}</span>
      <span class="node-tags">
        <span class="role-tag">{{ node.role }}</span>
        <span v-if="node.measured" class="tag meas-tag">measured</span>
        <span v-if="node.group" class="tag grp-tag">{{ node.group }}</span>
        <span v-if="node.nodes != null" class="tag node-tag">~{{ node.nodes }} node{{ node.nodes === 1 ? "" : "s" }}</span>
        <span v-if="node.note" class="tag">{{ node.note }}</span>
      </span>
    </div>

    <div v-if="node.children.length" class="branches">
      <div v-for="child in node.children" :key="child.index" class="branch">
        <span class="edge" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M4 12h14" /><path d="M13 6l6 6-6 6" /></svg>
        </span>
        <FlowChain :node="child" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.chain { display: flex; align-items: center; gap: 0.4rem; }
.branches { display: flex; flex-direction: column; gap: 0.5rem; }
.branch { display: flex; align-items: center; gap: 0.4rem; }
.edge { display: inline-flex; align-items: center; color: var(--text3); flex-shrink: 0; }
.edge svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.75;
  stroke-linecap: round; stroke-linejoin: round; }

.node { min-width: 10rem; border-radius: var(--r); padding: 0.45rem 0.6rem;
  border: 1px solid var(--border2); display: flex; flex-direction: column; gap: 0.25rem;
  background: var(--bg1); }
.node.victim { border-left: 3px solid var(--accent); background: var(--accent-glow); }
.node.aggressor { border-left: 3px solid var(--warn); }
.node.timed { border-left: 3px solid var(--text3); }
.node-name { font-size: 0.82rem; color: var(--text); word-break: break-all; }
.node-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.role-tag { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text2); }
.tag { font-size: 0.62rem; color: var(--text3); border: 1px solid var(--border);
  border-radius: 999px; padding: 0 0.4rem; }
.tag.grp-tag { color: var(--accent); border-color: var(--accent); }
.tag.meas-tag { color: var(--ok); border-color: var(--ok); }
.tag.node-tag { color: var(--text2); border-color: var(--border2); }
</style>
