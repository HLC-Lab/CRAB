// Renders the small, fixed set of ANSI SGR codes the engine's RichFormatter
// emits (crab.log.formatters) as styled HTML spans, so slurm_output.log reads
// the same in the browser as it does in a terminal. Not a general ANSI
// emulator: only reset/bold/dim and the 8 standard foreground colors.

const ANSI_RE = /\x1b\[([0-9;]*)m/g;

const COLOR_CLASSES: Record<number, string> = {
  30: "ansi-black",
  31: "ansi-red",
  32: "ansi-green",
  33: "ansi-yellow",
  34: "ansi-blue",
  35: "ansi-magenta",
  36: "ansi-cyan",
  37: "ansi-white",
};

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function ansiToHtml(text: string): string {
  let bold = false;
  let dim = false;
  let color: string | null = null;
  let out = "";
  let lastIndex = 0;

  const flush = (segment: string) => {
    if (!segment) return;
    const classes = [bold && "ansi-bold", dim && "ansi-dim", color].filter(Boolean).join(" ");
    const escaped = escapeHtml(segment);
    out += classes ? `<span class="${classes}">${escaped}</span>` : escaped;
  };

  ANSI_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = ANSI_RE.exec(text)) !== null) {
    flush(text.slice(lastIndex, match.index));
    lastIndex = ANSI_RE.lastIndex;

    const codes = match[1]
      .split(";")
      .filter((c) => c !== "")
      .map(Number);
    for (const code of codes.length ? codes : [0]) {
      if (code === 0) {
        bold = false;
        dim = false;
        color = null;
      } else if (code === 1) {
        bold = true;
      } else if (code === 2) {
        dim = true;
      } else if (code === 22) {
        bold = false;
        dim = false;
      } else if (code === 39) {
        color = null;
      } else if (code in COLOR_CLASSES) {
        color = COLOR_CLASSES[code];
      }
    }
  }
  flush(text.slice(lastIndex));
  return out;
}
