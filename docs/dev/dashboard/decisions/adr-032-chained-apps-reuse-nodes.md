# ADR-032 · Chained apps run on their predecessor's nodes; the partition decides

- **Date:** 2026-10-07
- **Status:** accepted

## Context

An app with `start: "sN"` waits until app N has finished. The allocator has always divided the
experiment's nodes between every app, chained or not, so a chain of four apps on one node left
three of them with no nodes, and a sequential sweep on half of the nodes had to ask for four
times as many. People writing chained configs expect the opposite: an app that runs after
another one uses the nodes the first one just released. Two shipped examples were written that
way and could not run.

## Decision

- A chained app runs on the nodes of the app it waits for, when it names no partition or the
  same partition. This is transitive: in A → B → C all three run on A's nodes.
- A chained app that names a different partition gets its share of that partition like any other
  member. The chain then only sets when it starts ("start when A ends, on other nodes").
- Apps that reuse nodes take no share: `split` and a partition's `split` divide the nodes between
  the other apps only (the chain heads).
- Two apps that would both reuse the same app's nodes would run at the same time on the same
  nodes. That is rejected before submitting, as are `sN` naming an app that does not exist and
  start cycles.
- No new config field and no schema bump: configs keep their shape, chained apps change meaning.

## Alternatives considered

- An explicit per-app field to opt into reuse: every chained config would need editing, and the
  default would keep surprising people.
- An opt-out field for "after A, on other nodes": partitions already express it.
- Always reuse, ignoring a different partition: the handoff case could not be written at all.
- Keep one slice per app: chained configs stay unrunnable unless they over-request nodes.

## Consequences

A config whose `split` listed chained apps fails the pre-submit check with a length error and has
to drop those entries. Concurrent apps sharing nodes (oversubscription) are still not supported;
that belongs to the local backend work (ADR-029). The dashboard, which turns a `split` into named
groups when loading a config, must not give chained apps a group of their own, or it would change
a chain into a handoff.
