<script setup lang="ts">
// The stage of a slow call: what is happening, what it waits for, how long it has waited and
// when it gives up. Reads the progress store, which the caller started with `watch(cluster)`.
// The region stays in the page while idle (empty, no height), so a screen reader that watches it
// hears each new stage. Only the stage text is announced; the counter is visual and changes
// every second.
import { computed } from "vue";
import { useProgressStore } from "@/stores/progress";
import { elapsedText, limitText, stageText } from "@/lib/progress";

const props = defineProps<{ cluster: string }>();
const progress = useProgressStore();

// The newest stage is the innermost one (stages are listed oldest first).
const stage = computed(() => {
  const snap = progress.current[props.cluster];
  const last = snap?.stages[snap.stages.length - 1];
  return snap && last ? { ...last, now: snap.now } : null;
});

const meta = computed(() => {
  const s = stage.value;
  if (!s) return "";
  const limit = limitText(s.timeout_s);
  return `${elapsedText(s.started_at, s.now)} waited${limit ? `, ${limit}` : ""}`;
});
</script>

<template>
  <div class="progress-line" role="status" aria-live="polite" data-testid="progress-line">
    <template v-if="stage">
      <span class="pulse" aria-hidden="true" />
      <span class="text">{{ stageText(stage) }}</span>
      <span class="meta" aria-hidden="true" data-testid="progress-meta">{{ meta }}</span>
    </template>
  </div>
</template>

<style scoped>
.progress-line {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  font-size: 0.85rem;
  color: var(--text2);
}
.pulse {
  align-self: center;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.2s ease-in-out infinite;
}
.text {
  color: var(--text);
}
.meta {
  font-family: var(--mono);
  font-size: 0.8rem;
  white-space: nowrap;
}
@keyframes pulse {
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .pulse {
    animation: none;
  }
}
</style>
