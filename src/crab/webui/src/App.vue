<script setup lang="ts">
import { onMounted } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useAppStore } from "@/stores/app";
import StatusBar from "@/components/StatusBar.vue";

const app = useAppStore();

const nav = [
  { to: "/remotes", label: "Remotes" },
  { to: "/author", label: "Author" },
  { to: "/wrappers", label: "Wrappers" },
  { to: "/jobs", label: "Jobs" },
  { to: "/results", label: "Results" },
];

onMounted(() => app.checkHealth());
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <span class="brand">CRAB</span>
      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="navlink"
          active-class="active"
          >{{ item.label }}</RouterLink
        >
      </nav>
      <button class="theme" title="Toggle theme" @click="app.toggleTheme()">
        {{ app.theme === "dark" ? "☀" : "☾" }}
      </button>
    </header>

    <main class="content">
      <RouterView />
    </main>

    <StatusBar />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0 1rem;
  height: 48px;
  background: var(--header-bg);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  backdrop-filter: blur(6px);
}
.brand {
  font-family: var(--sans);
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--accent);
}
.nav {
  display: flex;
  gap: 0.25rem;
}
.navlink {
  padding: 0.35rem 0.75rem;
  border-radius: var(--r);
  color: var(--text2);
}
.navlink:hover {
  color: var(--text);
  background: var(--sel-bg);
  text-decoration: none;
}
.navlink.active {
  color: var(--text);
  background: var(--sel-bg);
  box-shadow: inset 0 -2px 0 var(--accent);
}
.theme {
  margin-left: auto;
  background: none;
  border: 1px solid var(--border);
  color: var(--text2);
  border-radius: var(--r);
  cursor: pointer;
  width: 30px;
  height: 30px;
}
.theme:hover {
  border-color: var(--accent);
  color: var(--text);
}
.content {
  flex: 1;
}
</style>
