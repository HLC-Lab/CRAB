import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type {
  BootstrapPlan,
  CrabInfo,
  Profile,
  RemoteListItem,
  StepResult,
} from "@/api/types";

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export const useRemotesStore = defineStore("remotes", () => {
  const items = ref<RemoteListItem[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  // Per-remote transient state keyed by name.
  const busy = ref<Record<string, boolean>>({});
  const connectError = ref<Record<string, string>>({});
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
    try {
      const res = await api.remotes.connect(name, password);
      info.value[name] = res.info;
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
      // The SSH connection may be live even though the `crab info` handshake
      // failed (a non-zero exit means CRAB or its venv is missing, unparsable
      // output means it is too old or broken). Both leave the channel open, so
      // refresh to reflect the real state. Auth and connection-drop errors
      // leave nothing connected, so they simply show as disconnected.
      await refresh();
      const live = items.value.find((i) => i.name === name)?.connected;
      if (live) await loadPlan(name);
    } finally {
      busy.value[name] = false;
    }
  }

  async function disconnect(name: string) {
    busy.value[name] = true;
    try {
      await api.remotes.disconnect(name);
      delete info.value[name];
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
        delete connectError.value[name];
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
    items, loading, error, busy, connectError, info,
    plan, installResult, bootstrapBusy, bootstrapError,
    refresh, add, remove, connect, disconnect,
    loadPlan, install,
  };
});
