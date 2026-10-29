# Assignment validation

**Review result:** Pass for the timeboxed prototype with explicit limitations. The critical post-answer recovery defect found in the initial audit has been fixed and is covered by regression tests. Remaining gaps concern production retry/recovery behavior and the distinction between simulated future slots and durable allocation.

## Requirement checklist

| Assignment requirement | Result | Evidence / note |
|---|---|---|
| Working source and local setup README | Pass | Python package and PowerShell setup/run instructions are present. |
| Architecture diagram and concise architecture decision | Pass | `architecture.md` and ADR describe the modular Python/SQLite choice and tradeoffs. |
| Agent and call lifecycles | Pass | Explicit enums, transition maps, and state-machine documentation. |
| Progressive and predictive policies | Partial | Both policies exist. The regular predictive pacer creates a bounded proposal; Safety clamps it to agents available now. |
| Independent Safety Controller | Pass for regular path | `SafetyController` clamps by current free agents, queued borrowers, in-flight cap, and provider health. `PredictivePacer` has no provider dependency. |
| Concurrent agent/borrower allocation | Pass for current-capacity invariant | Transaction, conditional updates, partial unique indexes, and a two-thread allocation test prevent duplicate active reservations. |
| Two differently-behaved mock providers | Pass | Provider B injects timeouts, duplicate event IDs, and out-of-order callbacks. |
| Duplicate/out-of-order event behavior | Pass for modeled sequence | Event IDs deduplicate; invalid/stale transitions are ignored; terminal calls stay terminal. |
| Provider outage and new-call pacing | Partial | Three consecutive mock timeouts open the circuit, and regular simulation stops the remainder of an already-approved batch. Forced outage is covered. Automatic half-open recovery and retry backoff/limits are not implemented. |
| Agent disappearance during setup | Partial | A reserved setup call is failed and the agent remains offline. Coverage does not simulate an asynchronous provider response racing that disappearance. |
| Worker crash before provider initiation | Partial | Expired reservation test demonstrates lease cleanup. |
| Worker crash after ANSWERED / uncertain provider outcome | Pass after fix | Expired uncertain calls move to `RECONCILING`; the agent remains assigned, the borrower is held, and a late provider event or authoritative lookup resolves the call. Regression tests cover late completion and confirmed not-found. |
| Basic simulation and requested scenario metrics | Partial | Scenarios A-D and timed results exist. The timed simulator analytically models per-agent future slots and does not route through the durable Safety Controller or allocator. Its `connected_without_agent` metric is fixed at zero by construction. |
| Basic load test | Partial | Local single-thread SQLite baseline (100 reservations) is present; it is not a concurrent or large-scale stress test. |
| Scale/bottleneck discussion | Pass as design analysis | Documentation identifies SQLite write serialization, provider limits, callbacks, and campaign hotspots. No 1,000/10,000-agent benchmark is claimed. |
| Tests | Pass for prototype scope | 16 automated tests pass, including post-ANSWERED crash recovery, confirmed provider not-found, and circuit opening. |

## Initial audit defect and correction

The initial audit reproduced an expired lease after `ANSWERED` yielding:

```text
call: FAILED
borrower: QUEUED
agent: CONNECTED
```

That state was unsafe. Recovery now releases only calls that expired before provider initiation. Any call that may have reached the provider becomes `RECONCILING`; the borrower is held and the agent remains assigned until a late event or `resolve_reconciliation()` confirms the provider state. Regression coverage verifies late completion and confirmed not-found behavior.

## Remaining limitations / production follow-up

1. Add retry delay, retry attempt limits, and automated half-open recovery. The mock provider exposes a manual `recover()` hook; there is no scheduled retry policy.
2. Keep the timed future-slot simulation labeled as a modeled policy experiment. To use this in production, persist future reservations through the final safety/allocator boundary and reconcile early answers with the provider.
3. Expand outage and crash tests to multiple simultaneous in-flight calls and a provider lookup that reports an active call across repeated recovery passes.

## Verification performed

- `python -m unittest discover -s tests -v`: **16 tests passed** after the recovery/circuit-breaker fixes.
- Reproduced the initial post-`ANSWERED` defect, fixed it with a reconciliation state, and verified safe late-event and provider-lookup paths.
- Source/document inspection against the assignment's submission checklist and failure scenarios.
