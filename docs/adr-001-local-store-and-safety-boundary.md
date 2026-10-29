# ADR 001: Local store and safety boundary

**Status:** Accepted for prototype

## Context

The assignment asks for a small locally runnable demonstration of dialer correctness, concurrency, failures, and pacing within a 4-6 hour timebox. It explicitly discourages adding infrastructure for appearance. The most important property is preventing multiple active calls from claiming one agent and keeping predictive logic from bypassing safety.

## Decision

Use a modular Python command-line program with SQLite as its durable source of truth. Use transactions, conditional updates, partial unique indexes, event IDs, and reservation leases. Keep a separate Safety Controller between both policies and the allocator/provider path. Use deterministic in-process mock provider adapters.

## Consequences

The prototype is easy to run and inspect, has no external services, and makes the safety boundary testable. SQLite serializes writers, which limits throughput and is not a distributed lock service. The mock event loop does not reproduce network partitions or webhook authentication. These are explicit prototype limits rather than hidden guarantees.

## Revisit when

Multiple hosts must allocate concurrently, write contention or callback lag exceeds measured limits, or production provider integrations are introduced. Preserve the same invariants and use row-level locking/conditional transactions plus provider reconciliation when migrating.
