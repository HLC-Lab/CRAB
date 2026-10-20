<script setup lang="ts">
// Campaign-level settings. The dashboard writes no environment (ADR-027): jobs
// take theirs from the SbatchMan preset, so this card only says where to find it.
// (Campaign-wide sweep variables live in the Variables card, next to the group's own.)
</script>

<template>
  <small class="note">
    Jobs take their environment from the SbatchMan preset. On the cluster,
    <code>crab sbatchman-preset</code> prints the command that creates it (guide step 5).
  </small>
</template>

<style scoped>
.note {
  display: block;
  max-width: 38rem;
  color: var(--text3);
  font-size: var(--t-xs);
}
code {
  font-family: var(--mono);
}
</style>
