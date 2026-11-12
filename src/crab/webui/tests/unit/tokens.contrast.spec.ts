import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../src/styles/tokens.css", import.meta.url), "utf8");

// Custom properties of the block that starts with `selector {`.
function themeTokens(selector: string): Record<string, string> {
  const open = css.indexOf(`${selector} {`);
  if (open < 0) throw new Error(`block not found: ${selector}`);
  const body = css.slice(open, css.indexOf("}", open));
  const tokens: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens[m[1]] = m[2];
  }
  return tokens;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return (
    0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
  );
}

function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const themes: Array<[string, string]> = [
  ["dark", ":root"],
  ["light", "html.light"],
];
const surfaces = ["--bg", "--bg1", "--bg2"];

describe("contrast()", () => {
  it("is 21 for black on white and 1 for the same colour", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#336699", "#336699")).toBeCloseTo(1, 5);
  });
});

describe.each(themes)("%s theme text tokens", (_name, selector) => {
  const t = themeTokens(selector);

  it.each(["--text2", "--text3"])("%s is >= 4.5:1 on the surfaces", (token) => {
    for (const surface of surfaces) {
      const ratio = contrast(t[token], t[surface]);
      expect(
        ratio,
        `${token} ${t[token]} on ${surface} ${t[surface]} = ${ratio.toFixed(2)}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("--text3 stays dimmer than --text2", () => {
    expect(contrast(t["--text3"], t["--bg"])).toBeLessThan(contrast(t["--text2"], t["--bg"]));
  });
});
