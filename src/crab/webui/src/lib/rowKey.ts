// Stable v-for keys for editable, removable rows (plan 090 S11h). Index keys make
// Vue reuse the wrong row's DOM (focus, half-typed input) after a removal; the
// rows are plain objects with no id field, so key them by object identity.
const ids = new WeakMap<object, number>();
let next = 0;

export function rowKey(row: object): number {
  let id = ids.get(row);
  if (id === undefined) {
    id = ++next;
    ids.set(row, id);
  }
  return id;
}
