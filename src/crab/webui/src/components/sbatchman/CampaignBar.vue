<script setup lang="ts">
// Campaign-level settings. The dashboard writes no environment (ADR-045): jobs
// take theirs from the SbatchMan config, so this card only says where to find it.
// (Campaign-wide sweep variables live in the Variables card, next to the group's own.)
</script>

<template>
  <small class="note">
    Jobs take their environment from the SbatchMan config. Define it in your SbatchMan configs file
    (see the integration guide).
  </small>
</template>

<style scoped>
.note {
  display: block;
  max-width: 38rem;
  color: var(--text3);
  font-size: var(--t-xs);
}
</style>
