<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRemotesStore } from "@/stores/remotes";
import type { Profile } from "@/api/types";

const store = useRemotesStore();
const showAdd = ref(false);
const passwords = reactive<Record<string, string>>({});

const blank = (): Partial<Profile> => ({
  name: "",
  transport: "ssh",
  host: "",
  user: "",
  port: 22,
  auth: "agent",
  key_path: null,
  hostkey_policy: "strict",
  remote_crab: "~/CRAB",
  preset: "",
});
const form = reactive<Partial<Profile>>(blank());

onMounted(() => store.refresh());

async function submit() {
  const payload: Partial<Profile> = { ...form };
  if (!payload.host) delete (payload as any).host;
  const ok = await store.add(payload);
  if (ok) {
    Object.assign(form, blank());
    showAdd.value = false;
  }
}
</script>

<template>
  <section class="remotes">
    <header class="head">
      <h1>Remotes</h1>
      <div class="actions">
        <button class="btn" :disabled="store.loading" @click="store.refresh()">↻ Refresh</button>
        <button class="btn primary" @click="showAdd = !showAdd">
          {{ showAdd ? "Cancel" : "+ Add cluster" }}
        </button>
      </div>
    </header>

    <p v-if="store.error" class="banner err">{{ store.error }}</p>

    <!-- Add form -->
    <form v-if="showAdd" class="card form" @submit.prevent="submit">
      <div class="grid">
        <label>Name <input v-model="form.name" required placeholder="my-cluster" /></label>
        <label>Transport
          <select v-model="form.transport">
            <option value="ssh">ssh</option>
            <option value="local">local</option>
          </select>
        </label>
        <template v-if="form.transport === 'ssh'">
          <label>Host <input v-model="form.host" placeholder="login.hpc.example.org" /></label>
          <label>User <input v-model="form.user" placeholder="username" /></label>
          <label>Port <input v-model.number="form.port" type="number" /></label>
          <label>Auth
            <select v-model="form.auth">
              <option value="agent">agent (ssh-agent / cert)</option>
              <option value="key">key file</option>
              <option value="password">password</option>
            </select>
          </label>
          <label v-if="form.auth === 'key'">Key path <input v-model="form.key_path" placeholder="~/.ssh/id_ed25519" /></label>
          <label>Host key
            <select v-model="form.hostkey_policy">
              <option value="strict">strict</option>
              <option value="insecure">insecure (rotating login nodes)</option>
            </select>
          </label>
          <label>Remote CRAB <input v-model="form.remote_crab" placeholder="~/CRAB" /></label>
        </template>
        <label>Preset <input v-model="form.preset" placeholder="cluster preset" /></label>
      </div>
      <button class="btn primary" type="submit">Save</button>
    </form>

    <!-- List -->
    <p v-if="!store.items.length && !store.loading" class="empty">
      No clusters yet. Add one to get started.
    </p>

    <ul class="list">
      <li v-for="r in store.items" :key="r.name" class="card">
        <div class="row">
          <div class="ident">
            <span class="dot" :class="r.connected ? 'on' : 'off'" />
            <strong>{{ r.name }}</strong>
            <span class="sub">
              {{ r.transport === "local" ? "local" : `${r.user}@${r.host}` }}
              <span v-if="r.hostkey_policy === 'insecure'" class="warn">· hostkey:insecure</span>
            </span>
          </div>
          <div class="ctrls">
            <input
              v-if="r.auth === 'password' && !r.connected"
              v-model="passwords[r.name]"
              type="password"
              placeholder="password"
              class="pw"
            />
            <button
              v-if="!r.connected"
              class="btn"
              :disabled="store.busy[r.name]"
              @click="store.connect(r.name, passwords[r.name])"
            >
              {{ store.busy[r.name] ? "Connecting…" : "Connect" }}
            </button>
            <button v-else class="btn" :disabled="store.busy[r.name]" @click="store.disconnect(r.name)">
              Disconnect
            </button>
            <button class="btn danger" @click="store.remove(r.name)">Remove</button>
          </div>
        </div>

        <p v-if="store.connectError[r.name]" class="banner err small">
          {{ store.connectError[r.name] }}
        </p>

        <div v-if="store.info[r.name]" class="info">
          CRAB {{ store.info[r.name].crab_version }} · {{ store.info[r.name].crab_root }}
          <span class="presets">
            presets: {{ store.info[r.name].presets.map((p) => p.name).join(", ") || "none" }}
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.remotes { padding: 1.5rem 2rem; max-width: 64rem; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
h1 { font-family: var(--sans); font-size: 1.6rem; }
.actions { display: flex; gap: 0.5rem; }
.btn {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.8rem; cursor: pointer; font-family: var(--mono);
}
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: default; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger:hover { border-color: var(--danger); color: var(--danger); }
.card {
  background: var(--bg1); border: 1px solid var(--border);
  border-radius: var(--r2); padding: 1rem; margin-bottom: 0.75rem;
}
.form .grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 0.75rem; margin-bottom: 1rem;
}
label { display: flex; flex-direction: column; gap: 0.25rem; color: var(--text2); font-size: 0.8rem; }
input, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono);
}
.list { list-style: none; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.ident { display: flex; align-items: center; gap: 0.5rem; }
.sub { color: var(--text2); font-size: 0.85rem; }
.warn { color: var(--warn); }
.ctrls { display: flex; gap: 0.4rem; align-items: center; }
.pw { width: 9rem; }
.dot { width: 9px; height: 9px; border-radius: 50%; }
.dot.on { background: var(--ok); }
.dot.off { background: var(--text3); }
.info { margin-top: 0.6rem; color: var(--text2); font-size: 0.82rem; }
.presets { margin-left: 0.5rem; color: var(--text3); }
.banner { padding: 0.5rem 0.75rem; border-radius: var(--r); }
.banner.err { background: rgba(245, 101, 101, 0.12); color: var(--danger); border: 1px solid var(--danger); }
.banner.small { margin-top: 0.5rem; font-size: 0.8rem; }
.empty { color: var(--text3); padding: 2rem 0; }
</style>
