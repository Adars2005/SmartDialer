# State machines and recovery

## Agent lifecycle

```text
OFFLINE -> AVAILABLE -> RESERVED -> DIALING -> CONNECTED -> WRAP_UP -> AVAILABLE
                    \-> PAUSED -> AVAILABLE
```

Agent state changes for reservation, dialing, connection, and wrap-up are committed with their associated call event/record. `OFFLINE` and `PAUSED` agents are never allocated. `WRAP_UP` returns to available after the prototype's immediate post-call handling.

## Call lifecycle

```text
QUEUED -> RESERVED -> INITIATED -> RINGING -> ANSWERED -> CONNECTED -> COMPLETED
             |           |          |          |             |
             +-----------+----------+----------+-------------+-> FAILED
             +-----------+----------+----------+-------------+-> CANCELLED
                         \-> RECONCILING -> (RINGING / ANSWERED / CONNECTED / COMPLETED / FAILED)
```

The implementation validates forward transitions and permits direct terminal completion/failure when a provider skips intermediate callbacks. Duplicate event IDs are no-ops. Any event that would reopen a terminal call or move it along an invalid edge is recorded as ignored. The local mock does not define a provider sequence number, so it uses transition legality and terminal precedence rather than trusting wall-clock ordering.

## Resource invariants

- Every active call references one agent and one borrower.
- A partial unique index prevents more than one active call for an agent or borrower.
- The allocator reserves both rows and inserts the call in one transaction.
- Agent and borrower changes from provider events commit with the call state change.
- Only `AVAILABLE` agents and `QUEUED` borrowers can be reserved.

## Crash and timeout handling

Reservations have leases. On restart, `recover_expired()` safely fails a reservation that expired before initiation and releases its agent and borrower. Once a call may have reached the provider, recovery moves it to `RECONCILING`, keeps the agent assigned, and places the borrower on `HOLD`. A late valid provider event can resolve it; otherwise `resolve_reconciliation()` applies an authoritative provider lookup. Only a confirmed `not_found` or `failed` result makes the borrower eligible for retry. A confirmed active call retains its agent. This favors safety over freeing capacity quickly.

Provider initiation timeout is treated as uncertain and marked failed in this prototype. Production policy should distinguish definitive rejection from unknown outcome, preserve the reservation while reconciling, and avoid a second provider initiation until its state is known or a safe timeout expires.
