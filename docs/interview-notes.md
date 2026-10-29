# SmartDialer discussion notes

Use these notes to explain the prototype in a short technical walkthrough. Be precise about which guarantees come from the runnable allocator and which are assumptions in the timed simulation.

## 60-second overview

I built a locally runnable SmartDialer prototype with progressive and predictive pacing. Both policies submit a request to a separate Safety Controller; neither policy can call the provider directly. The controller checks current capacity and provider health, then the allocator reserves one agent and one borrower transactionally before it initiates a call. SQLite is the source of truth. Mock providers exercise timeouts, duplicate callbacks, and out-of-order callbacks. A regular simulator shows lifecycle and safety behavior, while a timed simulator models the setup-time benefit of reserving an agent's next slot.

## Architecture walkthrough

```text
Campaign/queues -> Progressive or Predictive policy -> Safety Controller
                                                     -> Atomic allocator
                                                     -> Provider adapter
Provider events -> Idempotent event handling -> SQLite source of truth
Lease recovery  -> Reconcile/release stale reservations
```

- **Progressive policy:** requests at most the currently available agent capacity.
- **Predictive policy:** estimates an extra request from answer rate, setup time, and talk time. For example, with 8 free agents, a 50% answer rate, and the configured aggressiveness, it may request 10 attempts.
- **Safety Controller:** rereads live state and reduces that request to current free-agent, queued-borrower, and in-flight capacity. In the durable prototype, the extra two are denied; predictive mode cannot bypass the controller.
- **Allocator:** uses a SQLite `BEGIN IMMEDIATE` transaction, conditional state updates, and partial unique indexes so competing workers cannot claim the same active agent or borrower.
- **Provider/event path:** adapters share an interface; event IDs are deduplicated, transitions are validated, and late events cannot reopen terminal calls.

## Concurrency answer

If two workers choose the same available agent, both attempt a conditional state update inside a serialized write transaction. Only one update can change `AVAILABLE` to `RESERVED`; the other observes no eligible row. Unique indexes provide another guard against two active calls sharing an agent or borrower. A test starts two worker threads against one agent and verifies only one reservation succeeds.

## Failure answers

- **Worker crash:** reservations have leases. Recovery marks expired uncertain calls failed and returns resources according to a conservative retry policy. A production provider adapter should query call status before retrying an uncertain initiation.
- **Provider outage:** the controller rejects new starts when provider health is false. Existing simulated connections continue to their scheduled end. Calls started before the outage cutoff can still resolve.
- **Duplicate callback:** the event ID is already in the inbox, so it has no second effect.
- **Out-of-order callback:** an invalid transition is recorded and ignored; terminal states remain terminal.
- **Agent disappears during setup:** the call is failed, the borrower can be retried, and the agent remains offline.

## What the timed results say

With 8 agents, 500 borrowers, 30 minutes, 5 seconds of setup, and Scenario B (50% answer rate, 90 seconds talk time), the timed model measured 92.5% utilization for progressive and 97.5% for predictive. With 3 seconds of extra provider latency, Provider B measured 85.6% and 91.9%. At the high-answer/high-talk Scenario C, both modes reached 100%, so predictive setup overlap did not increase completions in that horizon.

The timed simulator assigns one future call slot to a specific agent and schedules its answer at that agent's availability boundary. It reports a maximum of 8 connected calls and zero connected calls without an agent. This is an explicit simulation assumption, not a durable future-slot reservation implemented by the SQLite allocator.

## Honest limitation and next production step

The most important limitation is that the timed simulator's future-agent reservation is modeled in memory and analytically per agent. The production allocator only reserves currently available agents. To move this behavior into the real call path, I would persist future-slot reservations atomically, expire them when availability estimates change, and require a provider bridge/hold mechanism that prevents a borrower from becoming connected before the reserved agent can take the call. If the provider cannot guarantee that, the safety controller should fall back to ordinary progressive dialing.

SQLite write serialization is also a one-host prototype choice. At larger scale, I would move reservations to a row-locking relational store, shard campaign work, partition event processing by call/provider key, and keep the final capacity check at the allocation boundary.

## Final assignment question

Use forecasting to select the right borrower and prepare setup for an agent who is expected to become free, but make the Safety Controller and allocator own the hard capacity decision. The forecast can be wrong without creating an unassigned connected call: it either consumes a valid reserved agent slot or is reduced to progressive behavior. Keep the reservation durable and idempotent, reconcile provider uncertainty before retrying, and show the tradeoff with time-based simulations.
