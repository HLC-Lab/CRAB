# ADR-024 · Switch scatter/line charts to WebGL rendering (scattergl)

- **Date:** 2026-07-08
- **Status:** accepted

## Context

ADR-022's "Consequences" flagged Plotly's bundle weight as "accepted for v1, revisit only if it
proves a real problem." It did: the Compare workbench became noticeably slower to use as more
series were selected into one comparison, to the point of visibly lagging after only 4-5 series.
Measured with a realistic stress case (one job, 10 experiments x 3 apps x 5,000 rows each, no
cluster connected so network was ruled out): building the chart data stayed cheap and roughly flat
(18-46ms across 8 series), but each series' actual Plotly draw call grew from ~124ms to ~439ms as
more series were added. `plotly.js-cartesian-dist-min`'s `scatter`/`line` traces render as plain
SVG — one `<path>` element per data point — which does not scale past roughly a thousand points
per trace; a handful of multi-thousand-point series compounds quickly. Trying `type: "scattergl"`
against the existing bundle silently fell back to the same SVG path with no error, since that
package's trace-module list (`bar`, `box`, `contour`, `heatmap`, `histogram*`, `image`, `pie`,
`scatter`, `scatterternary`, `violin`) does not include the WebGL-backed `scattergl` module at
all.

## Decision

- **Replace `plotly.js-cartesian-dist-min` with a custom bundle** (`src/lib/plotlyBundle.ts`):
  `plotly.js/lib/core` plus only the trace modules this app actually renders (`scatter`,
  `scattergl`, `bar`, `violin`), registered once and exported as the shared `Plotly` instance
  every chart component imports.
- **`scatter`/`line` chart kinds now build `scattergl` traces**, not `scatter`
  (`resultsPlot.ts`'s `makePlotlyTraces`). `bar`/`violin` are unchanged (SVG, no equivalent
  point-count problem — an averaged bar or a violin's kernel density doesn't grow one DOM element
  per raw row).

## Alternatives considered

- **Downsample/aggregate points before plotting** (e.g. cap at N points per series, or bin and
  average) — avoids touching the rendering stack, but changes what the user sees (real data
  points silently dropped or averaged away) without their say, and adds a second thing
  (sampling strategy) to get right. Not pursued without the owner asking for it specifically.
- **Cap the number of series that can be compared at once** — simplest code change, but
  contradicts plan 077 decision 10's explicit goal (browse and compare freely across jobs and
  clusters) and papers over the actual bottleneck rather than fixing it.
- **Do nothing, accept the growing lag** — rejected; the owner's own report ("we should make it
  the fastest possible") and the measured degradation (toggle cost visibly growing with series
  count, not staying flat) make this the wrong default for a tool meant for exploratory
  comparison across many series.

## Consequences

Easier: comparing many series (the whole point of the Compare workbench, per ADR-022 and plan
077 decision 10) no longer visibly degrades as more are added — measured toggle cost stayed in a
roughly flat 190-400ms band across 8 series after this change, instead of growing from ~124ms to
~439ms. Harder: the minified bundle grew from ~1.43MB to ~1.55MB (~472KB to ~531KB gzipped) for
the WebGL/regl machinery `scattergl` pulls in — a further, accepted cost on top of ADR-022's
already-flagged bundle weight, still only loaded on Results-family routes (the router's existing
lazy code-splitting is untouched). If this bundle size becomes its own problem, the next lever is
route- or bundle-splitting `scattergl` out from the rest of the chart code, not reverting to SVG
scatter.

**Follow-up (2026-07-09) — compatibility fallback.** `scattergl` requires a real WebGL context;
without one, Plotly does not fall back to SVG on its own — confirmed live (Chromium launched with
`--disable-webgl`/`--disable-gpu`) that it instead draws a "WebGL is not supported by your
browser" placeholder in place of the chart, a silent total failure to show any data on a
GPU-less/sandboxed/older environment. `resultsPlot.ts` gained `hasWebglSupport()` (memoized
feature detection: creates a throwaway canvas, checks `getContext("webgl")`) so
`makePlotlyTraces` only requests `scattergl` when a context is actually available, falling back to
plain `scatter` otherwise — the same safe SVG path this ADR's decision moved away from as the
default, kept as an automatic fallback rather than removed. Verified live in both directions
(WebGL disabled: real chart data renders via SVG, not the placeholder; WebGL enabled: still
renders via a `<canvas>`, confirming the fallback doesn't accidentally win when GPU support is
present).
