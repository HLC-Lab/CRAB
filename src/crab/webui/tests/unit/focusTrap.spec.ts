import { describe, expect, it } from "vitest";
import { isEscape, nextFocusIndex } from "@/lib/focusTrap";

describe("nextFocusIndex", () => {
  it("moves forward one step", () => {
    expect(nextFocusIndex(0, 3, false)).toBe(1);
    expect(nextFocusIndex(1, 3, false)).toBe(2);
  });

  it("moves backward one step", () => {
    expect(nextFocusIndex(2, 3, true)).toBe(1);
    expect(nextFocusIndex(1, 3, true)).toBe(0);
  });

  it("wraps from the last element to the first on Tab", () => {
    expect(nextFocusIndex(2, 3, false)).toBe(0);
  });

  it("wraps from the first element to the last on Shift+Tab", () => {
    expect(nextFocusIndex(0, 3, true)).toBe(2);
  });

  it("enters from the first element when focus is outside the list and Tab is pressed", () => {
    expect(nextFocusIndex(-1, 3, false)).toBe(0);
    expect(nextFocusIndex(7, 3, false)).toBe(0);
  });

  it("enters from the last element when focus is outside the list and Shift+Tab is pressed", () => {
    expect(nextFocusIndex(-1, 3, true)).toBe(2);
    expect(nextFocusIndex(7, 3, true)).toBe(2);
  });

  it("keeps focus on a single focusable in both directions", () => {
    expect(nextFocusIndex(0, 1, false)).toBe(0);
    expect(nextFocusIndex(0, 1, true)).toBe(0);
    expect(nextFocusIndex(-1, 1, false)).toBe(0);
    expect(nextFocusIndex(-1, 1, true)).toBe(0);
  });

  it("returns -1 when there is nothing to focus", () => {
    expect(nextFocusIndex(-1, 0, false)).toBe(-1);
    expect(nextFocusIndex(0, 0, true)).toBe(-1);
  });
});

describe("isEscape", () => {
  it("accepts Escape", () => {
    expect(isEscape("Escape")).toBe(true);
  });

  it("accepts the legacy Esc key name", () => {
    expect(isEscape("Esc")).toBe(true);
  });

  it("rejects other keys", () => {
    expect(isEscape("Enter")).toBe(false);
    expect(isEscape("Tab")).toBe(false);
    expect(isEscape("e")).toBe(false);
    expect(isEscape("")).toBe(false);
  });
});
