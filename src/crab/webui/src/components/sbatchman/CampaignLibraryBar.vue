<script setup lang="ts">
// Campaign save/load library (plan 086), mirroring components/author/LibraryBar.vue:
// New / Browse… / Save, a searchable Browse overlay (hover duplicate/delete), and
// a discard-unsaved-changes guard gated on store.isDirty. Unlike the Author page,
// the campaign name is an always-visible field here (no Basics tab to hide it in),
// so there is no separate name-prompt-on-save modal.
import { computed, ref } from "vue";
import type { CampaignEntry } from "@/api/types";
import { useSbatchmanStore } from "@/stores/sbatchman";
import ConfirmModal from "@/components/ConfirmModal.vue";

const emit = defineEmits<{
  // Fired after the campaign actually changed underneath the parent (a fresh
  // blank campaign, or a loaded library entry), so it can reset pane selection.
  new: [];
  opened: [];
}>();

const store = useSbatchmanStore();

// A saved entry's own group count, without deriving a full SbatchmanCampaign
// (that needs a live store, not an arbitrary library entry).
function groupCount(e: CampaignEntry): number {
  const spec = e.spec as { groups?: unknown[] };
  return Array.isArray(spec.groups) ? spec.groups.length : 0;
}

// Browse overlay (searchable library picker)
const showOpen = ref(false);
const openQuery = ref("");
const filteredLibrary = computed(() => {
  const q = openQuery.value.trim().toLowerCase();
  const lib = store.library;
  return q ? lib.filter((e) => e.name.toLowerCase().includes(q)) : lib;
});
async function openEntry(id: string) {
  await store.open(id);
  emit("opened");
  showOpen.value = false;
  openQuery.value = "";
}

function duplicateLibraryEntry(id: string): void {
  store.duplicateCampaign(id);
  showOpen.value = false;
  openQuery.value = "";
}

const removeLibraryTarget = ref<{ id: string; name: string } | null>(null);
function requestRemoveLibraryEntry(id: string, name: string): void {
  removeLibraryTarget.value = { id, name };
}
async function confirmRemoveLibraryEntry(): Promise<void> {
  if (removeLibraryTarget.value) await store.removeCampaign(removeLibraryTarget.value.id);
  removeLibraryTarget.value = null;
}

// Discard-unsaved-changes guard for "+ New" and "Browse…", gated on real
// dirtiness (store.isDirty).
const showDiscardConfirm = ref(false);
const pendingAction = ref<"new" | "open" | null>(null);

function requestNew(): void {
  if (store.isDirty) {
    pendingAction.value = "new";
    showDiscardConfirm.value = true;
  } else {
    store.newCampaign();
    emit("new");
  }
}
function requestBrowse(): void {
  if (store.isDirty) {
    pendingAction.value = "open";
    showDiscardConfirm.value = true;
  } else {
    showOpen.value = true;
  }
}
function confirmDiscard(): void {
  showDiscardConfirm.value = false;
  if (pendingAction.value === "new") {
    store.newCampaign();
    emit("new");
  } else if (pendingAction.value === "open") {
    showOpen.value = true;
  }
  pendingAction.value = null;
}
</script>

<template>
  <header class="bar">
    <div class="grp">
      <button class="btn" @click="requestNew">+ New</button>
      <button class="btn" @click="requestBrowse">Browse…</button>
      <input
        v-model="store.name"
        class="name"
        placeholder="Campaign name"
        title="Campaign name (used for the saved entry and the pushed YAML filename)"
      />
      <button class="btn primary" :disabled="store.busy" @click="store.save">
        {{ store.busy ? "Saving…" : "Save" }}
      </button>
    </div>
  </header>

  <!-- Discard-unsaved-changes confirm, for New/Browse when the campaign is dirty -->
  <ConfirmModal
    v-if="showDiscardConfirm"
    title="Discard unsaved changes?"
    :message="`${pendingAction === 'new' ? 'Starting a new campaign' : 'Opening another campaign'} will discard the changes you haven't saved.`"
    confirm-label="Discard"
    @confirm="confirmDiscard"
    @cancel="showDiscardConfirm = false"
  />

  <!-- Browse overlay: searchable library picker with hover duplicate/delete -->
  <div v-if="showOpen" class="modal-bg" @click.self="showOpen = false">
    <div class="modal card open-modal">
      <input v-model="openQuery" class="search" placeholder="Search saved campaigns…" autofocus />
      <ul class="open-list">
        <li
          v-for="e in filteredLibrary"
          :key="e.id"
          :class="{ current: e.id === store.entryId }"
          @click="openEntry(e.id)"
        >
          <div class="open-main">
            <span class="open-name">{{ e.name }}</span>
            <span class="open-actions">
              <button
                type="button"
                class="icon-btn"
                title="Duplicate campaign"
                @click.stop="duplicateLibraryEntry(e.id)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="9" y="9" width="11" height="11" rx="2" />
                  <path d="M5 15V5a2 2 0 0 1 2-2h10" />
                </svg>
              </button>
              <button
                type="button"
                class="icon-btn danger"
                title="Delete campaign"
                @click.stop="requestRemoveLibraryEntry(e.id, e.name)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 7h14" />
                  <path d="M9 7V5h6v2" />
                  <path d="M7 7l1 13h8l1-13" />
                </svg>
              </button>
            </span>
          </div>
          <div class="open-sub">
            <span class="open-count"
              >{{ groupCount(e) }} group{{ groupCount(e) === 1 ? "" : "s" }}</span
            >
            <span class="open-date">Updated {{ e.updated_at.slice(0, 10) }}</span>
          </div>
        </li>
        <li v-if="!filteredLibrary.length" class="empty">
          {{ store.library.length ? "No matching campaigns." : "No saved campaigns yet." }}
        </li>
      </ul>
    </div>
  </div>

  <ConfirmModal
    v-if="removeLibraryTarget"
    title="Delete this campaign?"
    :message="`Delete “${removeLibraryTarget.name}”? This cannot be undone.`"
    confirm-label="Delete"
    @confirm="confirmRemoveLibraryEntry"
    @cancel="removeLibraryTarget = null"
  />
</template>

<style scoped>
.bar {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 1rem;
}
.grp {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text2);
  border-radius: var(--r);
  padding: 0.2rem;
  cursor: pointer;
}
.icon-btn:hover {
  border-color: var(--border);
  color: var(--text);
}
.icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.name {
  width: 16rem;
}
input,
textarea,
select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
}

.empty {
  color: var(--text3);
  font-size: var(--t-md);
}

.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: min(40rem, 92vw);
  padding: 1.25rem;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}

/* Open overlay */
.open-modal {
  width: min(34rem, 92vw);
  padding: 0.75rem;
}
.search {
  width: 100%;
  margin-bottom: 0.6rem;
  font-size: var(--t-md);
  padding: 0.5rem 0.6rem;
}
.open-list {
  list-style: none;
  max-height: 24rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.open-list li {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.55rem 0.65rem;
  border: 1px solid transparent;
  border-radius: var(--r);
  cursor: pointer;
}
.open-list li:hover {
  background: var(--bg2);
}
.open-list li.current {
  border-color: var(--accent);
}
.open-main {
  position: relative;
  display: flex;
  align-items: center;
  padding-right: 3.6rem;
}
.open-name {
  color: var(--text);
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.open-actions {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  opacity: 0;
  transition: opacity 0.1s ease;
}
.open-list li:hover .open-actions {
  opacity: 1;
}
.open-sub {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.open-count {
  color: var(--text2);
  font-size: var(--t-sm);
  font-weight: 500;
}
.open-date {
  color: var(--text3);
  font-size: var(--t-xs);
}
</style>
