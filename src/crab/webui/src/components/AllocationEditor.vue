<script setup lang="ts">
// Node-allocation editor as a drag-to-divide bar (ported from the approved
// alloc-v5 mockup). It operates ONLY via `alloc.partitions` (the "slices"):
// zero slices means solo (all nodes run one workload, no `allocation` emitted);
// dividing seeds victim/aggressor at an even 50/50. Even splits keep an empty
// share (the bar computes "50%"); an unequal divide writes explicit shares.
// `alloc.by` is always "groups"; `alloc.split` is unused by this UI.
//
// The "total nodes" field is a LOCAL illustration total (the real node count is
// a job-level Basics field): counts and the placement strip are a preview, not
// the engine's exact runtime placement.
import { computed, ref } from "vue";
import { type AllocationDraft, emptyPartition } from "@/lib/config";
import NumberField from "@/components/NumberField.vue";

const props = defineProps<{
  alloc: AllocationDraft;
  // When the caller owns the placement mode (e.g. the per-experiment override),
  // hide this component's own mode segmented control + stride/seed.
  hideMode?: boolean;
}>();
const a = computed(() => props.alloc);

const COLORS = ["#6ea8fe", "#ff8c78", "#7ec699", "#b69cff", "#e0b352", "#56c2c2"];
const DEFAULT_NAMES = ["victim", "aggressor"];

const barRef = ref<HTMLElement | null>(null);
const total = ref("8"); // illustration only

const partitions = computed(() => a.value.partitions);
const solo = computed(() => partitions.value.length === 0);
const totalN = computed(() => Math.max(1, parseInt(total.value || "1", 10) || 1));
const strideN = computed(() => Math.max(1, parseInt(a.value.stride || "1", 10) || 1));
const seedN = computed(() => parseInt(a.value.seed || "0", 10) || 0);

// Readable ink for a slice's background colour.
function ink(c: string): string {
  const n = parseInt(c.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return (r * 299 + g * 587 + b * 114) / 1000 > 150 ? "#0b1220" : "#eceef2";
}
function colorOf(i: number): string {
  return COLORS[i % COLORS.length];
}
function placeholder(i: number): string {
  return DEFAULT_NAMES[i] ?? `group ${i + 1}`;
}
function nameOf(i: number): string {
  return partitions.value[i]?.name.trim() || placeholder(i);
}

// -- Share model: partitions carry a string share ("" = even). The bar works in
// integer percentages summing to 100; even splits read as the equal division. --
function equalShares(n: number): number[] {
  const base = Math.floor(100 / n);
  const arr = Array(n).fill(base);
  const rem = 100 - base * n;
  for (let i = 0; i < rem; i++) arr[i]++;
  return arr;
}
function readShares(): number[] {
  const ps = partitions.value;
  const n = ps.length;
  if (!n) return [];
  const anySet = ps.some((p) => p.share.trim() !== "");
  if (!anySet) return equalShares(n);
  return ps.map((p) => {
    const v = parseInt(p.share.trim(), 10);
    return Number.isFinite(v) ? v : Math.round(100 / n);
  });
}
// Write shares back, honouring "even = omit share": an exact equal division
// stores "" on every slice; otherwise explicit integer shares.
function writeShares(nums: number[]): void {
  const ps = partitions.value;
  const n = nums.length;
  const even = n > 0 && 100 % n === 0 && nums.every((x) => x === 100 / n);
  ps.forEach((p, i) => {
    p.share = even ? "" : String(nums[i]);
  });
}

const shares = computed(readShares);

// -- Node counts per slice for the given total (mockup counts(), ≥1 floor). ---
function counts(N: number, sh: number[]): number[] {
  if (sh.length <= 1) return [N];
  const exact = sh.map((s) => (N * s) / 100);
  const base = exact.map((x) => Math.floor(x));
  const miss = N - base.reduce((x, y) => x + y, 0);
  const rem = exact.map((x, i) => [i, x - base[i]] as [number, number]).sort((x, y) => y[1] - x[1]);
  for (let k = 0; k < Math.min(miss, rem.length); k++) base[rem[k][0]]++;
  for (let i = 0; i < base.length; i++) {
    if (base[i] < 1) {
      let bi = 0;
      base.forEach((c, j) => {
        if (c > base[bi]) bi = j;
      });
      if (base[bi] > 1) {
        base[bi]--;
        base[i]++;
      }
    }
  }
  return base;
}
// -- Owner index per rendered cell, ordered by placement mode (mockup owners()). -
function owners(mode: string, cs: number[], stride: number, seed: number): number[] {
  const N = cs.reduce((x, y) => x + y, 0);
  if (mode === "interleaved") {
    const o: number[] = [];
    const cap = cs.slice();
    while (o.length < N) {
      let adv = false;
      for (let p = 0; p < cap.length; p++) {
        const t = Math.min(stride, cap[p]);
        for (let k = 0; k < t; k++) {
          o.push(p);
          cap[p]--;
          adv = true;
        }
      }
      if (!adv) break;
    }
    return o;
  }
  const lin: number[] = [];
  cs.forEach((c, p) => {
    for (let k = 0; k < c; k++) lin.push(p);
  });
  if (mode === "random") {
    const arr = lin.slice();
    let s = (seed * 2654435761) >>> 0 || 1;
    for (let i = arr.length - 1; i > 0; i--) {
      s = (s * 1103515245 + 12345) & 0x7fffffff;
      const j = s % (i + 1);
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }
  return lin;
}

const nodeCounts = computed(() => counts(totalN.value, shares.value));

// The placement strip: capped at 64 cells, wraps to a matrix above 32.
const strip = computed(() => {
  const T = totalN.value;
  const cs = nodeCounts.value.slice();
  const shown = Math.min(T, 64);
  let sc = cs;
  if (T > 64) sc = cs.map((c) => Math.max(1, Math.round((c / T) * 64)));
  let d = shown - sc.reduce((x, y) => x + y, 0);
  for (let k = 0; k < Math.abs(d); k++) {
    let idx = 0;
    sc.forEach((c, j) => {
      if (d > 0 ? c > sc[idx] : c < sc[idx]) idx = j;
    });
    sc[idx] += Math.sign(d);
  }
  return owners(a.value.mode, sc, strideN.value, seedN.value);
});
const isMatrix = computed(() => Math.min(totalN.value, 64) > 32);

const caption = computed(() => {
  const T = totalN.value;
  const desc =
    a.value.mode === "interleaved"
      ? `groups alternate every ${strideN.value} node${strideN.value === 1 ? "" : "s"}`
      : a.value.mode === "random"
        ? `node assignment shuffled (seed ${seedN.value})`
        : "each group gets a contiguous block";
  const cap = T > 64 ? `. showing 64 of ${T}` : "";
  return `${desc}${cap}. Illustration of placement, exact assignment happens at runtime.`;
});

// -- Slice mutations --------------------------------------------------------
function addSlice(): void {
  const ps = a.value.partitions;
  a.value.by = "groups";
  if (ps.length === 0) {
    ps.push(emptyPartition("victim"), emptyPartition("aggressor")); // even 50/50
    return;
  }
  const sh = readShares();
  let bi = 0;
  sh.forEach((s, i) => {
    if (s > sh[bi]) bi = i;
  });
  const g = Math.max(1, Math.round(sh[bi] / 2));
  sh[bi] -= g;
  ps.push(emptyPartition(""));
  sh.push(g);
  writeShares(sh);
}
function removeSlice(i: number): void {
  const ps = a.value.partitions;
  const sh = readShares();
  const g = sh[i] ?? 0;
  ps.splice(i, 1);
  sh.splice(i, 1);
  if (ps.length <= 1) {
    ps.splice(0); // back to solo
    return;
  }
  sh[0] += g;
  writeShares(sh);
}

// Typed percentage: clamp 1..99 and rebalance the others to sum 100.
function onPct(i: number, raw: string): void {
  let val = parseInt(raw, 10);
  if (Number.isNaN(val)) return;
  val = Math.max(1, Math.min(99, val));
  const sh = readShares();
  sh[i] = val;
  const others = sh.map((_, j) => j).filter((j) => j !== i);
  const rem = 100 - val;
  const ot = others.reduce((x, j) => x + sh[j], 0) || 1;
  let acc = 0;
  others.forEach((j, k) => {
    if (k < others.length - 1) {
      sh[j] = Math.max(1, Math.round((rem * sh[j]) / ot));
      acc += sh[j];
    } else {
      sh[j] = Math.max(1, rem - acc);
    }
  });
  writeShares(sh);
}

function setMode(m: AllocationDraft["mode"]): void {
  a.value.mode = m;
}

// Drag a divider: repartition the two adjacent slices by the pointer position.
function startDrag(e: PointerEvent, i: number): void {
  e.preventDefault();
  const el = barRef.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const start = readShares();
  const tot = start[i] + start[i + 1];
  let before = 0;
  for (let k = 0; k < i; k++) before += start[k];
  const move = (ev: PointerEvent) => {
    const x = ((ev.clientX - rect.left) / rect.width) * 100;
    const left = Math.max(3, Math.min(tot - 3, x - before));
    const sh = readShares();
    sh[i] = Math.round(left);
    sh[i + 1] = Math.round(tot - left);
    writeShares(sh);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
</script>

<template>
  <div class="alloc">
    <p class="lede">
      By default the machine runs one workload. Divide the nodes when you want two apps to share the
      machine and contend, then choose how they are placed. Apps attach to a slice in the experiment editor.
    </p>

    <div class="headrow">
      <span class="sect">Divide the nodes</span>
      <label class="total">total nodes
        <NumberField v-model="total" :min="1" class="totfield" />
      </label>
    </div>

    <!-- Solo: no division -->
    <div v-if="solo" class="solo">
      <div class="barmini" />
      <div class="txt">
        <h3>All nodes run one workload</h3>
        <p>No division. Add a slice to have two apps share the machine.</p>
      </div>
    </div>

    <!-- Division bar -->
    <div v-else ref="barRef" class="bar">
      <template v-for="(p, i) in partitions" :key="i">
        <div
          class="slice"
          :style="{ flex: shares[i], background: colorOf(i), color: ink(colorOf(i)) }"
        >
          <div class="top">
            <span class="dot" :style="{ background: ink(colorOf(i)), opacity: 0.5 }" />
            <input v-model="p.name" class="nm" :placeholder="placeholder(i)" />
          </div>
          <div class="meta">
            <div class="cnt">{{ nodeCounts[i] }} node{{ nodeCounts[i] === 1 ? "" : "s" }}</div>
            <div class="pctwrap">
              <input
                class="pct"
                type="number"
                min="1"
                max="99"
                :value="shares[i]"
                :style="{ color: ink(colorOf(i)) }"
                @input="onPct(i, ($event.target as HTMLInputElement).value)"
              />
              <span class="u">%</span>
            </div>
          </div>
          <button class="rm" title="remove slice" :style="{ color: ink(colorOf(i)) }" @click="removeSlice(i)">&times;</button>
        </div>
        <div v-if="i < partitions.length - 1" class="handle" @pointerdown="startDrag($event, i)" />
      </template>
    </div>

    <div class="actions">
      <button class="add" @click="addSlice">{{ solo ? "+ divide the nodes" : "+ add a slice" }}</button>
      <span v-if="!solo" class="metatxt">{{ partitions.length }} slices, drag a divider or type a %</span>
    </div>

    <!-- Placement -->
    <div v-if="!solo" class="layout">
      <span class="sect">Placement</span>
      <div class="lrow">
        <div v-if="!hideMode" class="seg">
          <button :class="{ on: a.mode === 'linear' }" @click="setMode('linear')">linear</button>
          <button :class="{ on: a.mode === 'interleaved' }" @click="setMode('interleaved')">interleaved</button>
          <button :class="{ on: a.mode === 'random' }" @click="setMode('random')">random</button>
        </div>
        <label v-if="!hideMode && a.mode === 'interleaved'" class="subc">every
          <NumberField v-model="a.stride" :min="1" class="subfield" /> node(s)</label>
        <label v-if="!hideMode && a.mode === 'random'" class="subc">seed
          <NumberField v-model="a.seed" class="subfield" /></label>
      </div>
      <div class="strip" :class="{ matrix: isMatrix }">
        <span v-for="(o, i) in strip" :key="i" class="c" :style="{ background: colorOf(o) }" />
      </div>
      <div class="legend">
        <span v-for="(p, i) in partitions" :key="i" class="k">
          <span class="sw" :style="{ background: colorOf(i) }" />
          <b>{{ nameOf(i) }}</b> · {{ nodeCounts[i] }} node{{ nodeCounts[i] === 1 ? "" : "s" }}
        </span>
      </div>
      <p class="cap">{{ caption }}</p>
    </div>
  </div>
</template>

<style scoped>
.alloc { display: flex; flex-direction: column; gap: 0.2rem; }
.lede { color: var(--text2); font-size: var(--t-sm); line-height: 1.45; margin-bottom: 1rem; max-width: 42rem; }

.headrow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 0.8rem; }
.total { display: flex; align-items: center; gap: 0.5rem; color: var(--text2); font-size: var(--t-sm); font-family: var(--sans); }
.totfield { width: 4.4rem; }
.sect { font-size: var(--t-sm); color: var(--text3); text-transform: uppercase; letter-spacing: 0.07em;
  font-weight: 600; font-family: var(--sans); }

.bar { display: flex; height: 104px; border-radius: var(--r2); overflow: hidden; background: var(--bg1);
  border: 1px solid var(--border); user-select: none; }
.slice { position: relative; display: flex; flex-direction: column; justify-content: space-between;
  padding: 0.75rem 0.85rem; min-width: 0; }
.slice .top { display: flex; align-items: center; gap: 0.4rem; }
.slice .dot { width: 9px; height: 9px; border-radius: 3px; flex: 0 0 9px; }
.slice input.nm { all: unset; font-family: var(--sans); font-weight: 600; font-size: var(--t-md);
  min-width: 0; flex: 1; text-overflow: ellipsis; cursor: text; }
.slice input.nm::placeholder { opacity: 0.6; font-weight: 500; }
.slice .cnt { font-family: var(--mono); font-size: var(--t-sm); }
.pctwrap { display: inline-flex; align-items: center; gap: 1px; margin-top: 0.15rem; }
.slice input.pct { all: unset; font-family: var(--mono); font-size: var(--t-sm); width: 2.4rem; opacity: 0.85;
  cursor: text; border-bottom: 1px dashed transparent; }
.slice input.pct:hover, .slice input.pct:focus { border-bottom-color: currentColor; }
.pctwrap .u { font-family: var(--mono); font-size: var(--t-sm); opacity: 0.7; }
.slice .rm { position: absolute; top: 0.55rem; right: 0.55rem; width: 20px; height: 20px; border: none;
  cursor: pointer; background: transparent; border-radius: 5px; opacity: 0; font-size: 14px; line-height: 1;
  display: flex; align-items: center; justify-content: center; }
.slice:hover .rm { opacity: 1; }
.slice .rm:hover { background: rgba(255, 255, 255, 0.12); }
.handle { width: 14px; margin: 0 -7px; position: relative; z-index: 4; cursor: col-resize; flex: 0 0 14px; }
.handle::after { content: ""; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 3px; height: 40px; border-radius: 2px; background: var(--bg); opacity: 0.55; }
.handle:hover::after { opacity: 0.9; background: #fff; }

.actions { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.85rem; }
.add { background: transparent; border: 1px dashed var(--border2); color: var(--text2); border-radius: var(--r);
  padding: 0.4rem 0.8rem; cursor: pointer; font-size: var(--t-sm); font-family: var(--sans); }
.add:hover { border-color: var(--accent); color: var(--accent); }
.metatxt { color: var(--text3); font-size: var(--t-sm); }

.solo { background: var(--bg1); border: 1px solid var(--border); border-radius: var(--r2);
  padding: 1.4rem 1.5rem; display: flex; align-items: center; gap: 1rem; }
.solo .barmini { flex: 1; height: 44px; border-radius: var(--r); background: #6ea8fe; opacity: 0.9; }
.solo .txt h3 { margin: 0 0 0.15rem; font-size: var(--t-md); font-weight: 600; font-family: var(--sans); }
.solo .txt p { margin: 0; color: var(--text2); font-size: var(--t-sm); }

.layout { margin-top: 2rem; display: flex; flex-direction: column; gap: 0.2rem; }
.lrow { display: flex; align-items: center; gap: 1.2rem; flex-wrap: wrap; margin: 0.7rem 0 1rem; }
.seg { display: inline-flex; background: var(--bg2); border: 1px solid var(--border); border-radius: var(--r); overflow: hidden; }
.seg button { background: transparent; border: none; color: var(--text2); padding: 0.42rem 1rem; cursor: pointer;
  font-size: var(--t-sm); font-family: var(--sans); }
.seg button.on { background: var(--accent); color: #fff; font-weight: 600; }
.subc { display: flex; align-items: center; gap: 0.45rem; color: var(--text2); font-size: var(--t-sm); font-family: var(--sans); }
.subfield { width: 3.8rem; }

.strip { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 3px; }
.strip.matrix { grid-auto-flow: row; grid-template-columns: repeat(auto-fill, 18px); justify-content: start; }
.strip .c { height: 30px; border-radius: 5px; }
.strip.matrix .c { height: 18px; border-radius: 3px; }
.legend { display: flex; gap: 1.3rem; flex-wrap: wrap; margin-top: 0.7rem; font-size: var(--t-sm); color: var(--text2); }
.legend .k { display: inline-flex; align-items: center; gap: 0.4rem; }
.legend .sw { width: 11px; height: 11px; border-radius: 3px; }
.legend .k b { color: var(--text); font-weight: 600; font-family: var(--sans); }
.cap { color: var(--text3); font-size: var(--t-sm); margin-top: 0.55rem; }
</style>
