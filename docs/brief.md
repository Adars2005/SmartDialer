# SmartDialer architecture and repository brief

## End-to-end architecture

```text
Campaign + Agent/Borrower Pools
              |
     Dialing Policy Selector
        /             \
Progressive       Predictive Pacing
        \             /   (request only)
         Safety Controller
                |
       Atomic Call Allocator
                |
         Provider Interface
          /             \
   Mock Provider A   Mock Provider B
                |
      Provider Event Handling
                |
       State/Reconciliation
                |
       SQLite (source of truth)
```

Progressive and predictive policies both request work through the Safety Controller. Predictive pacing cannot call a provider. The controller clamps requests using current free-agent and borrower capacity, the in-flight limit, and provider health. The allocator persists agent, borrower, and call reservations before initiation. SQLite is authoritative for the runnable prototype.

The two mock providers share an interface. Provider A is fast and reliable; Provider B injects timeouts, duplicate event IDs, and out-of-order events. The event handler deduplicates callbacks and validates state transitions. Expired pre-initiation reservations can be released. If a call may have reached the provider, recovery keeps the agent assigned and borrower held in `RECONCILING` until an event or authoritative lookup resolves it.

## Core safety rules

- Every active durable call has exactly one reserved agent and borrower.
- A provider call cannot be initiated until those reservations commit.
- A connected call remains associated with its agent until terminal status or reconciliation.
- Predictive pacing proposes work; the Safety Controller and allocator enforce capacity.
- Duplicate events have no second effect, and stale events cannot reopen terminal calls.

## Repository layout

```text
SmartDialer/
├── README.md
├── pyproject.toml
├── src/smartdialer/
│   ├── cli.py                       # simulation and load-test commands
│   ├── dialers.py                   # progressive and predictive policies
│   ├── domain.py                    # entities, states, and transition rules
│   ├── providers.py                 # provider interface and mock providers
│   ├── safety.py                    # independent initiation checks
│   ├── simulation.py                # scenarios, metrics, and load harness
│   └── store.py                     # SQLite persistence and allocation
├── tests/
│   ├── test_safety_and_events.py
│   └── test_simulation.py
└── docs/
    ├── architecture.md
    ├── state-machines.md
    ├── adr-001-local-store-and-safety-boundary.md
    ├── simulation-results.md
    ├── assignment-validation.md
    ├── interview-notes.md
    └── status.md
```

## Scope and limitations

This is a locally runnable CLI prototype, not a production dialer. SQLite is suitable for the one-host demonstration; write serialization is the first likely storage bottleneck as worker concurrency grows. The timed predictive simulator models future agent slots analytically, while durable allocation supports currently available agents only. The mock circuit opens after three consecutive timeouts, but scheduled retry backoff and automatic half-open recovery are not implemented. See [assignment-validation.md](assignment-validation.md) for the full checklist and [simulation-results.md](simulation-results.md) for reproducible measurements.
