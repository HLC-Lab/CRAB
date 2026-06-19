<script setup lang="ts">
import { computed } from "vue";
import { useAppStore } from "@/stores/app";

const app = useAppStore();
const online = computed(() => app.health !== null);
</script>

<template>
  <footer class="statusbar">
    <span class="dot" :class="{ ok: online, bad: !online }" />
    <span v-if="online">
      backend ok · CRAB {{ app.health!.crab_version }} · api v{{ app.health!.api_schema }}
    </span>
    <span v-else class="err">{{ app.backendError ?? "connecting…" }}</span>
    <button class="refresh" title="Re-check backend" @click="app.checkHealth()">↻</button>
  </footer>
</template>

<style scoped>
.statusbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 1rem;
  border-top: 1px solid var(--border);
  background: var(--bg1);
  color: var(--text2);
  font-size: 0.8rem;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot.ok {
  background: var(--ok);
}
.dot.bad {
  background: var(--danger);
}
.err {
  color: var(--danger);
}
.refresh {
  margin-left: auto;
  background: none;
  border: 1px solid var(--border);
  color: var(--text2);
  border-radius: var(--r);
  cursor: pointer;
  padding: 0 0.4rem;
}
.refresh:hover {
  border-color: var(--accent);
  color: var(--text);
}
</style>
