import { describe, expect, it } from "vitest";
import { nameTaken, takenMessage } from "@/lib/remoteName";

const ITEMS = [{ name: "leonardo" }, { name: "alps" }];

describe("nameTaken", () => {
  it("is true for a name another remote already has", () => {
    expect(nameTaken(ITEMS, "leonardo", null)).toBe(true);
    expect(nameTaken(ITEMS, "alps", null)).toBe(true);
  });

  it("is false for a free name", () => {
    expect(nameTaken(ITEMS, "leonardo2", null)).toBe(false);
    expect(nameTaken([], "leonardo", null)).toBe(false);
  });

  it("is false for an empty name (the required field says so)", () => {
    expect(nameTaken(ITEMS, "", null)).toBe(false);
  });

  it("matches the backend: exact, case sensitive", () => {
    expect(nameTaken(ITEMS, "Leonardo", null)).toBe(false);
  });

  it("lets an edit keep its own name but not take another remote's", () => {
    expect(nameTaken(ITEMS, "leonardo", "leonardo")).toBe(false);
    expect(nameTaken(ITEMS, "alps", "leonardo")).toBe(true);
    expect(nameTaken(ITEMS, "renamed", "leonardo")).toBe(false);
  });
});

describe("takenMessage", () => {
  it("says what to do, without a long dash", () => {
    const text = takenMessage("leonardo");
    expect(text).toBe("A remote named 'leonardo' already exists. Choose another name.");
    expect(text).not.toMatch(/[–—]/);
  });
});
