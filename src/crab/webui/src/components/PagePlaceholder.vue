<script setup lang="ts">
defineProps<{ title: string; phase: string; blurb: string }>();
</script>

<template>
  <section class="placeholder">
    <h1>{{ title }}</h1>
    <p class="blurb">{{ blurb }}</p>
    <p class="phase">Lands in {{ phase }}.</p>
  </section>
</template>

<style scoped>
.placeholder {
  padding: 2rem;
  max-width: 60rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.6rem;
  margin-bottom: 0.5rem;
}
.blurb {
  color: var(--text2);
  max-width: 40rem;
}
.phase {
  margin-top: 1rem;
  color: var(--text3);
  font-size: 0.85rem;
}
</style>
