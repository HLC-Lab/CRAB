# ADR-010 · Presets are chosen at submit time, not stored in configs

- **Date:** 2026-06-19
- **Status:** accepted

## Context

A preset (`config/presets.json` entry) describes a cluster environment: env vars, sbatch
defaults, module lines. Early authoring designs included a preset picker in the editor.

## Decision

The experiment config stays system-independent; the preset is a `crab run -p <preset>`
argument selected when submitting (a submit/monitor-phase concern). No preset field exists in
authored configs.

## Alternatives considered

- Preset as a config field — binds a reusable experiment description to one cluster and
  duplicates information the cluster already owns.

## Consequences

Easier: one config runs on any cluster; sharing configs between researchers stays clean.
Harder: the submit flow must offer the preset choice with sensible defaults per remote.
