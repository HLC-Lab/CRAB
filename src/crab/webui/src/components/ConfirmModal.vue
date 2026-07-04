<script setup lang="ts">
defineProps<{
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
}>();
const emit = defineEmits<{ confirm: []; cancel: [] }>();
</script>

<template>
  <div class="modal-bg" @click.self="emit('cancel')">
    <div class="modal">
      <h2>{{ title }}</h2>
      <p class="hint">{{ message }}</p>
      <div class="modal-actions">
        <button class="btn" @click="emit('cancel')">{{ cancelLabel ?? "Cancel" }}</button>
        <button class="btn danger" @click="emit('confirm')">{{ confirmLabel ?? "Delete" }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* !important: this component's root div also carries its host's own scope-id
   attribute (Vue scoped-CSS lets a parent style a child's root element), so a
   plain z-index here ties in specificity with the host's own ".modal-bg" rule
   and loses on source order. This must win regardless of where it's mounted. */
.modal-bg { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center; z-index: 60 !important; }
.modal { width: min(28rem, 92vw); padding: 1.25rem; background: var(--bg1);
  border: 1px solid var(--border); border-radius: var(--r2); }
.modal h2 { font-family: var(--sans); font-size: var(--t-lg); margin-bottom: 0.3rem; }
.modal .hint { color: var(--text2); font-size: var(--t-md); margin-bottom: 0.75rem; }
.modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.75rem; }
.btn { background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.8rem; cursor: pointer; font-family: var(--sans); }
.btn:hover { border-color: var(--accent); }
.btn.danger { border-color: var(--danger); color: var(--danger); }
.btn.danger:hover { background: var(--danger); color: var(--text); }
</style>
