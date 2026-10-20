<script setup lang="ts">
// Sortable/searchable table over one experiment's rows, with column
// visibility toggles. Ported from crab_dashboard.html's TableRenderer module
// (search/sort/column-default logic lives in lib/resultsTable.ts, pure and
// unit-tested there); restyled to this app's design tokens.
import { computed, ref, watch } from "vue";
import { formatVal, type ResultRow } from "@/lib/resultsChart";
import {
  defaultVisibleColumns,
  filterRows,
  MAX_DISPLAY_ROWS,
  sortRows,
  type SortDir,
} from "@/lib/resultsTable";

const props = defineProps<{
  rows: ResultRow[];
}>();

const allColumns = computed(() => (props.rows.length ? Object.keys(props.rows[0]) : []));
const visibleColumns = ref<Set<string>>(new Set());

// Reset the visible-column selection whenever the column set itself changes
// (e.g. switching to a different experiment/app type), same as the legacy
// dashboard's rebuildDropdown.
watch(
  allColumns,
  (cols) => {
    visibleColumns.value = defaultVisibleColumns(cols);
  },
  { immediate: true },
);

const columns = computed(() => allColumns.value.filter((c) => visibleColumns.value.has(c)));

function toggleColumn(col: string) {
  const next = new Set(visibleColumns.value);
  if (next.has(col)) next.delete(col);
  else next.add(col);
  visibleColumns.value = next;
}

const search = ref("");
const sortCol = ref<string | null>(null);
const sortDir = ref<SortDir>(1);

function sortBy(col: string) {
  sortDir.value = sortCol.value === col ? ((sortDir.value * -1) as SortDir) : 1;
  sortCol.value = col;
}

const filteredSorted = computed(() => {
  const filtered = filterRows(props.rows, columns.value, search.value);
  return sortRows(filtered, sortCol.value, sortDir.value);
});

const displayRows = computed(() => filteredSorted.value.slice(0, MAX_DISPLAY_ROWS));
const truncated = computed(() => filteredSorted.value.length > MAX_DISPLAY_ROWS);
</script>

<template>
  <div class="results-table">
    <div class="toolbar">
      <input v-model="search" type="search" placeholder="Search…" class="search" />
      <details class="col-picker">
        <summary>Columns</summary>
        <label v-for="c in allColumns" :key="c" class="col-opt">
          <input type="checkbox" :checked="visibleColumns.has(c)" @change="toggleColumn(c)" />
          {{ c }}
        </label>
      </details>
      <span class="count">
        {{ filteredSorted.length.toLocaleString() }} row{{ filteredSorted.length === 1 ? "" : "s" }}
      </span>
    </div>

    <p v-if="truncated" class="truncated-note">
      Showing the first {{ MAX_DISPLAY_ROWS.toLocaleString() }} rows; narrow the search to see more.
    </p>

    <div class="table-wrap">
      <table v-if="displayRows.length">
        <thead>
          <tr>
            <th v-for="c in columns" :key="c" @click="sortBy(c)">
              {{ c }}
              <span class="sort-indicator">{{
                sortCol === c ? (sortDir > 0 ? "↑" : "↓") : "↕"
              }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in displayRows" :key="i">
            <td v-for="c in columns" :key="c">{{ formatVal(c, row[c]) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty">No rows match this search.</p>
    </div>
  </div>
</template>

<style scoped>
.results-table {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.search {
  background: var(--bg2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.3rem 0.6rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
}
.col-picker summary {
  cursor: pointer;
  font-size: var(--t-sm);
  color: var(--text2);
}
.col-picker {
  position: relative;
}
.col-opt {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: var(--t-sm);
  padding: 0.15rem 0;
}
.count {
  margin-left: auto;
  color: var(--text3);
  font-size: var(--t-sm);
}
.truncated-note {
  color: var(--text3);
  font-size: var(--t-sm);
}
.table-wrap {
  overflow: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: var(--t-sm);
}
th {
  cursor: pointer;
  text-align: left;
  padding: 0.35rem 0.6rem;
  color: var(--text2);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.sort-indicator {
  color: var(--text3);
}
td {
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.empty {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 1rem;
}
</style>
