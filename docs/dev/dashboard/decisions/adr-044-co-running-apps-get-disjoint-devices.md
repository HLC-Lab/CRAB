# ADR-044 · Co-running apps get disjoint devices

- **Date:** 2026-11-03
- **Status:** accepted

## Context

`bind.gpu: "per-rank"` ([ADR-043](adr-043-placement-intent-and-topology-facts.md)) gave each app's ranks
GPUs 0, 1, 2 and so on, whatever else ran on the node. Two apps that ran at the same time
([ADR-033](adr-033-execution-settings.md), [ADR-036](adr-036-corun-srun-and-app-ranks.md)) therefore both
started at GPU 0: they shared GPUs without saying so, and the measurement was invalid. Cores already avoid
this, since `bind.cores: "nearest"` never gives the same core to two apps. GPUs and NICs had no such rule.

## Decision

**Devices are held per node and per chain.** Apps of one chain (a head and the apps that `start` after
it) run one after the other on the head's nodes, so they never conflict. Apps of different chains that
share a node do.

- With `bind.gpu: "per-rank"`, an app takes the GPUs of the node in the usual `order`, skipping the GPUs an
  earlier app of another chain holds on a node they share. An app holds its `per-rank` GPUs and its literal
  `gpus`; `"shared"`, `"all"` and `"none"` hold nothing.
- When too few GPUs are free, the run stops before it is submitted, and the message names the app, the
  GPUs it needs, how many are free and how many other apps hold.
- A chained app may use the GPUs and NICs of its own head and avoids those of the apps that run beside it.
- `"nearest"` for the NIC follows the GPU, so disjoint GPUs give different NICs where the node pairs them.
  The other picks may share NICs between apps on purpose.
- `bind.nic` `exclusive` also keeps the NIC to its app on a node: a later app cannot name a NIC an exclusive
  app holds, and an exclusive app cannot take a NIC an earlier app uses. It only checks apps that name
  NICs (a pick other than `"default"`, or a literal `nics`); an app with `"nic": "default"` lets UCX choose.
- None of this depends on `allocation.cores`.

## Alternatives considered

- Only warn: a silent collision invalidates the measurement, and a warning is easy to miss.
- Refuse any overlap and let the user offset by hand: no key offsets GPUs, so a valid placement could not
  be written.
- Disjoint NICs for every pick: sharing a NIC between apps is a deliberate congestion experiment.
- Make `allocation.cores: "shared"` also share devices: devices and cores are separate decisions.

## Consequences

A config that relied on two apps sharing GPU 0 now gets different GPUs for them, and fails with a message
when the node has too few. The way to share on purpose is `"gpu": "shared"` on those apps. This is a
breaking change, which 1.x allows ([ADR-042](adr-042-stable-from-2-0.md)). `exclusive` protects only
against apps that name NICs: an app with `"nic": "default"` can still use the port.
