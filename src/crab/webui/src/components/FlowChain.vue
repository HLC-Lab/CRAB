<script setup lang="ts">
// One app in the flow diagram plus the apps that start after it, drawn as a
// dependency tree: the node on the left, an arrow per dependent, each dependent
// rendered recursively to the right. Roots (apps that start at run start or
// after a delay) are stacked vertically by the parent (they run together).
// End-behavior (victim/aggressor/timed) is not shown here; it's already the
// app's own "Ends" dropdown in the editor below. This diagram is structure
// and grouping only, colored by the app's allocation group instead of its role.
import { type FlowTree } from "@/lib/config";

defineProps<{ node: FlowTree }>();
</script>

<template>
  <div class="chain">
    <div class="node" :style="{ borderLeftColor: node.color || 'var(--border2)' }">
      <span v-if="node.measured" class="meas-badge" title="Metrics are collected for this app"
        >M</span
      >
      <span class="node-name">{{ node.name }}</span>
      <span v-if="node.group || node.nodes != null || node.note" class="node-foot">
        <span v-if="node.group">{{ node.group }}</span>
        <span v-if="node.group && node.nodes != null"> &middot; </span>
        <span v-if="node.nodes != null"
          >{{ node.nodes }} node{{ node.nodes === 1 ? "" : "s" }}</span
        >
        <span v-if="node.note" class="note">{{ node.note }}</span>
      </span>
    </div>

    <div v-if="node.children.length" class="branches">
      <div v-for="child in node.children" :key="child.index" class="branch">
        <span class="edge" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 12h14" />
            <path d="M13 6l6 6-6 6" />
          </svg>
        </span>
        <FlowChain :node="child" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.chain {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.branches {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.branch {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.edge {
  display: inline-flex;
  align-items: center;
  color: var(--text3);
  flex-shrink: 0;
}
.edge svg {
  width: 22px;
  height: 22px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.node {
  position: relative;
  min-width: 10rem;
  border-radius: var(--r);
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border2);
  border-left-width: 3px;
  border-left-style: solid;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  background: var(--bg1);
}
.node-name {
  font-size: 0.82rem;
  color: var(--text);
  word-break: break-all;
  padding-right: 1.1rem;
}
.node-foot {
  font-size: 0.68rem;
  color: var(--text3);
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.meas-badge {
  position: absolute;
  top: 0.4rem;
  right: 0.5rem;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--ok);
  color: #06210f;
  font-size: 0.55rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
</style>
