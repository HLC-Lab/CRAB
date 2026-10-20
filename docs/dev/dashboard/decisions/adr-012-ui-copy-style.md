# ADR-012 · UI copy: plain language, real defaults, no long dashes

- **Date:** 2026-06-20
- **Status:** accepted

## Context

Early screens over-explained every field, used typographic em/en dashes inconsistently, and
put fake example values ("my_use_case", "10") in placeholders where they read as real values.

## Decision

UI text is plain and human: the CLI/docs term as the visible label, at most one short hint and
only where a field is non-obvious. No em or en dashes anywhere in user-facing strings (checked
by grep before frontend commits). Placeholders show the engine's real default (ghost-styled),
never an invented example; tri-state selects name their unset option explicitly ("default" /
"inherit"). Sans-serif for UI text; monospace only for code, paths, and values. The whole
config is called a "use case" wherever the file as a whole is meant.

## Alternatives considered

- Tooltip-everything — hides the one hint that matters behind hover.
- Typographically "nicer" punctuation — inconsistent across fonts and reads as generated text.

## Consequences

Easier: screens are calmer and truthful about defaults. Harder: contributors must know the
dash rule (the verification gate greps for it) and resist re-adding per-field prose.
