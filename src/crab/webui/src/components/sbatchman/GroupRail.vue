<script setup lang="ts">
// Left rail: navigator over campaign groups, mirroring AuthorRail's
// experiments zone. Selection is owned by the caller (SbatchmanView).
defineProps<{
  groups: Array<{ tag: string }>;
  selected: number;
  jobsForGroup: (i: number) => number;
}>();
const emit = defineEmits<{
  select: [number];
  add: [];
  "request-remove": [number];
}>();
</script>

<template>
  <aside class="rail">
    <div class="zone">
      <div class="zlabel">
        <span>Groups</span>
        <button class="add" title="Add group" @click="emit('add')">+</button>
      </div>
      <ul>
        <li
          v-for="(g, i) in groups"
          :key="i"
          :class="{ on: selected === i }"
          @click="emit('select', i)"
        >
          <span class="g-name">{{ g.tag || "untitled group" }}</span>
          <span class="g-meta"
            >{{ jobsForGroup(i) }} job{{ jobsForGroup(i) === 1 ? "" : "s" }}</span
          >
          <span class="g-actions">
            <button
              type="button"
              class="icon-btn danger"
              title="Remove group"
              @click.stop="emit('request-remove', i)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 7h14" />
                <path d="M9 7V5h6v2" />
                <path d="M7 7l1 13h8l1-13" />
              </svg>
            </button>
          </span>
        </li>
        <li v-if="!groups.length" class="empty nav-empty">No groups yet.</li>
      </ul>
    </div>
  </aside>
</template>

<style scoped>
.rail {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.zone {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.zlabel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--sans);
  color: var(--text3);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  padding: 0 0.2rem;
}
.zone .add {
  background: transparent;
  border: none;
  color: var(--text3);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  padding: 0;
}
.zone .add:hover {
  color: var(--text);
}
.zone ul {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.zone li {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 2.4rem 0.4rem 0.55rem;
  border: 1px solid transparent;
  border-radius: var(--r);
  cursor: pointer;
  color: var(--text2);
  font-size: var(--t-md);
}
.zone li:hover {
  background: var(--bg2);
  color: var(--text);
}
.zone li.on {
  background: var(--bg2);
  border-color: var(--accent);
  color: var(--text);
}
.g-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.g-meta {
  color: var(--text3);
  font-size: var(--t-sm);
  flex-shrink: 0;
}
.nav-empty {
  cursor: default;
  color: var(--text3);
}
.nav-empty:hover {
  background: transparent;
}
.g-meta,
.g-actions {
  position: absolute;
  right: 0.55rem;
  top: 50%;
  transform: translateY(-50%);
}
.g-meta {
  transition: opacity 0.1s ease;
}
.g-actions {
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  opacity: 0;
}
.zone li:hover .g-actions {
  opacity: 1;
}
.zone li:hover .g-meta {
  opacity: 0;
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

.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
</style>
