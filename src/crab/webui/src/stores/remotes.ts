import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { BootstrapPlan, CrabInfo, Profile, RemoteListItem, StepResult } from "@/api/types";

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export const useRemotesStore = defineStore("remotes", () => {
  const items = ref<RemoteListItem[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  // Per-remote transient state keyed by name.
  const busy = ref<Record<string, boolean>>({});
  // Real SSH/auth/connection failures, shown verbatim.
  const connectError = ref<Record<string, string>>({});
  // Connected, but CRAB isn't installed there → drives the guided install.
  const crabMissing = ref<Record<string, boolean>>({});
  const info = ref<Record<string, CrabInfo>>({});

  // Bootstrap (guided install) state, keyed by remote name.
  const plan = ref<Record<string, BootstrapPlan>>({});
  const installResult = ref<Record<string, StepResult>>({});
  const bootstrapBusy = ref<Record<string, boolean>>({});
  const bootstrapError = ref<Record<string, string>>({});

  async function refresh() {
    loading.value = true;
    error.value = null;
    try {
      items.value = await api.remotes.list();
    } catch (e) {
      error.value = msg(e);
    } finally {
      loading.value = false;
    }
  }

  async function add(profile: Partial<Profile>) {
    error.value = null;
    try {
      await api.remotes.add(profile);
      await refresh();
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    }
  }

  async function update(name: string, profile: Partial<Profile>) {
    error.value = null;
    try {
      await api.remotes.update(name, profile);
      await refresh();
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    }
  }

  async function remove(name: string) {
    try {
      await api.remotes.remove(name);
      await refresh();
    } catch (e) {
      error.value = msg(e);
    }
  }

  async function connect(name: string, password?: string) {
    busy.value[name] = true;
    delete connectError.value[name];
    delete crabMissing.value[name];
    try {
      const res = await api.remotes.connect(name, password);
      if (res.crab_installed && res.info) {
        info.value[name] = res.info;
      } else {
        // Connected, but CRAB isn't there — offer to install it.
        crabMissing.value[name] = true;
        await loadPlan(name);
      }
      await refresh();
    } catch (e) {
      // Real SSH/auth/connection failure (nothing connected).
      connectError.value[name] = msg(e);
      await refresh();
    } finally {
      busy.value[name] = false;
    }
  }

  async function disconnect(name: string) {
    busy.value[name] = true;
    try {
      await api.remotes.disconnect(name);
      delete info.value[name];
      delete crabMissing.value[name];
      delete plan.value[name];
      delete installResult.value[name];
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
    } finally {
      busy.value[name] = false;
    }
  }

  // -- Guided bootstrap ------------------------------------------------------
  async function loadPlan(name: string) {
    bootstrapBusy.value[name] = true;
    delete bootstrapError.value[name];
    delete installResult.value[name];
    try {
      plan.value[name] = await api.remotes.bootstrap.plan(name);
    } catch (e) {
      bootstrapError.value[name] = msg(e);
    } finally {
      bootstrapBusy.value[name] = false;
    }
  }

  // Run the whole install, then check whether CRAB is now usable.
  async function install(name: string, preCommands: string[]) {
    bootstrapBusy.value[name] = true;
    delete bootstrapError.value[name];
    try {
      installResult.value[name] = await api.remotes.bootstrap.install(name, preCommands);
      const res = await api.remotes.bootstrap.verify(name);
      if (res.installed && res.info) {
        info.value[name] = res.info;
        delete crabMissing.value[name];
        delete plan.value[name];
        delete installResult.value[name];
      } else {
        bootstrapError.value[name] =
          res.reason ?? "CRAB still isn't usable. Check the install output below.";
      }
      await refresh();
    } catch (e) {
      bootstrapError.value[name] = msg(e);
    } finally {
      bootstrapBusy.value[name] = false;
    }
  }

  return {
    items,
    loading,
    error,
    busy,
    connectError,
    crabMissing,
    info,
    plan,
    installResult,
    bootstrapBusy,
    bootstrapError,
    refresh,
    add,
    update,
    remove,
    connect,
    disconnect,
    loadPlan,
    install,
  };
});
