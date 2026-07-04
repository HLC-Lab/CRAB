<script setup lang="ts">
// GLOBAL · Node allocation: thin wrapper around AllocationEditor bound to the
// draft's global allocation.
import { useAuthorStore } from "@/stores/author";
import AllocationEditor from "@/components/AllocationEditor.vue";

const store = useAuthorStore();
const d = store.draft;
</script>

<template>
  <h2 class="pane-title">Node allocation</h2>
  <AllocationEditor :alloc="d.allocation" :numnodes="d.numnodes" />
</template>

<style scoped>
.pane-title {
  font-family: var(--sans);
  font-size: var(--t-lg);
  color: var(--text);
}
</style>
