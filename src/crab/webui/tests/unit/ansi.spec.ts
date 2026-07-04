/**
 * The engine's worker logger (RichFormatter) colorizes the raw slurm_output.log
 * with a small fixed set of SGR codes, meant for terminal viewing. This module
 * renders the same information as styled HTML for the web log viewer.
 */
import { describe, expect, it } from "vitest";

import { ansiToHtml } from "@/lib/ansi";

describe("ansiToHtml", () => {
  it("passes plain text through unchanged, HTML-escaped", () => {
    expect(ansiToHtml("hello world")).toBe("hello world");
  });

  it("escapes HTML-significant characters even with no ANSI codes", () => {
    expect(ansiToHtml("a < b & c > d")).toBe("a &lt; b &amp; c &gt; d");
  });

  it("wraps a colored segment in a span with the matching class", () => {
    expect(ansiToHtml("\x1b[32mINFO\x1b[0m")).toBe('<span class="ansi-green">INFO</span>');
  });

  it("applies bold and color together for compound codes", () => {
    expect(ansiToHtml("\x1b[1;31mFATAL\x1b[0m")).toBe(
      '<span class="ansi-bold ansi-red">FATAL</span>',
    );
  });

  it("applies dim to tree-drawing separators", () => {
    expect(ansiToHtml("\x1b[2m|\x1b[0m")).toBe('<span class="ansi-dim">|</span>');
  });

  it("resets state at a bare reset code, leaving following text unstyled", () => {
    expect(ansiToHtml("\x1b[34mCRAB\x1b[0m plain")).toBe(
      '<span class="ansi-blue">CRAB</span> plain',
    );
  });

  it("escapes HTML inside a colored segment", () => {
    expect(ansiToHtml("\x1b[35m<script>\x1b[0m")).toBe(
      '<span class="ansi-magenta">&lt;script&gt;</span>',
    );
  });

  it("handles a realistic mixed line with multiple segments", () => {
    const line = "\x1b[2m[19:55:18]\x1b[0m \x1b[32mINFO \x1b[0m \x1b[34mCRAB\x1b[0m Worker started";
    expect(ansiToHtml(line)).toBe(
      '<span class="ansi-dim">[19:55:18]</span> ' +
        '<span class="ansi-green">INFO </span> ' +
        '<span class="ansi-blue">CRAB</span> Worker started',
    );
  });
});
