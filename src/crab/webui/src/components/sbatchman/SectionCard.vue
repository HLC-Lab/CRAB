<script setup lang="ts">
// One numbered card of the SbatchMan page: a header (step number, title, one-line
// subtitle) over a body. The number is left out for cards that are not a step.
defineProps<{
  title: string;
  subtitle: string;
  n?: number;
}>();
</script>

<template>
  <section class="card">
    <header class="head">
      <span v-if="n !== undefined" class="n">{{ n }}</span>
      <h2>{{ title }}</h2>
      <p>{{ subtitle }}</p>
    </header>
    <div class="body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  overflow: hidden;
  min-width: 0;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 0.7rem;
  padding: 0.65rem 1.1rem;
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  border-left: 3px solid var(--accent);
}
.n {
  font-family: var(--sans);
  font-weight: 800;
  color: var(--accent);
  font-size: var(--t-md);
}
h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  font-weight: 700;
  color: var(--text);
}
p {
  margin: 0 0 0 auto;
  color: var(--text3);
  font-size: var(--t-sm);
  font-family: var(--sans);
  text-align: right;
}
.body {
  padding: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
</style>
