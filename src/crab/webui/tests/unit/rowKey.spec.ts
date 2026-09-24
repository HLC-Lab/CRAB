/** Stable v-for keys for removable rows (plan 090 S11h). */
import { reactive } from "vue";
import { describe, expect, it } from "vitest";
import { rowKey } from "@/lib/rowKey";

describe("rowKey", () => {
  it("gives each row object its own key, stable across removals", () => {
    const rows = reactive([{ name: "a" }, { name: "b" }, { name: "c" }]);
    const keyOfB = rowKey(rows[1]);
    const keyOfC = rowKey(rows[2]);
    expect(new Set([rowKey(rows[0]), keyOfB, keyOfC]).size).toBe(3);

    rows.splice(0, 1); // remove "a": index keys would shift, identity keys must not
    expect(rowKey(rows[0])).toBe(keyOfB);
    expect(rowKey(rows[1])).toBe(keyOfC);
  });
});
