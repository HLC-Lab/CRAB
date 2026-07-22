import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { Health } from "@/api/types";
import { isSbatchmanMode } from "@/lib/mode";

const THEME_KEY = "crab.theme";

export const useAppStore = defineStore("app", () => {
  const health = ref<Health | null>(null);
  const backendError = ref<string | null>(null);
  // SbatchMan integration mode (plan 084): read once from the injected meta tag.
  const sbatchman = ref(isSbatchmanMode());
  const theme = ref<"dark" | "light">(
    (localStorage.getItem(THEME_KEY) as "dark" | "light") || "dark",
  );

  function applyTheme() {
    document.documentElement.classList.toggle("light", theme.value === "light");
    document.documentElement.classList.toggle("dark", theme.value === "dark");
  }

  function toggleTheme() {
    theme.value = theme.value === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, theme.value);
    applyTheme();
  }

  async function checkHealth() {
    backendError.value = null;
    try {
      health.value = await api.health();
    } catch (e) {
      health.value = null;
      backendError.value = e instanceof ApiError ? e.message : "Backend unreachable";
    }
  }

  return { health, backendError, sbatchman, theme, applyTheme, toggleTheme, checkHealth };
});
