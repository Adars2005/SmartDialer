# Simulation and load-test notes

**Run date:** 2026-10-27. Seeds and parameters are included below. These results come from local simulations; they are reproducibility checks, not production capacity claims.

## Scenario runs

Command-equivalent inputs: 8 agents, 50 borrowers, seed 17, max 20 ticks. Progressive and predictive policies were run against Provider A for each scenario.

| Scenario | Answer rate / talk time | Attempts | Connected | Failed | Modeled utilization | Borrowers left |
|---|---:|---:|---:|---:|---:|---:|
| A | 20% / 120 sec | 160 | 23 | 137 | 14.4% | 27 |
| B | 50% / 90 sec | 113 | 50 | 63 | 39.1% | 0 |
| C | 70% / 180 sec | 85 | 50 | 35 | 44.6% | 0 |
| D | Changing / 90 sec | 133 | 39 | 94 | 24.4% | 11 |

In this synchronous simulator both policies produce the same call outcomes. Predictive pacing makes larger proposals based on estimated answer rate, while Safety reduces those proposals to live free-agent and borrower capacity. The timed comparison below models setup overlap separately.

## Timed setup-overlap comparison

Inputs: 8 agents, 500 borrowers, 1,800 second horizon, Scenario B (50% answer rate, 90 second talk time), Provider A, 5 second setup, seed 7.

| Mode | Attempts | Connected | Failed | Predials | Setup time saved | Modeled utilization | Max connected | Connected without agent |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Progressive | 295 | 148 | 147 | 0 | 0 sec | 92.5% | 8 | 0 |
| Predictive | 307 | 156 | 151 | 148 | 355 sec | 97.5% | 8 | 0 |

The timed model reserves a specific agent's next call slot before pre-dialing. It schedules the answer at the boundary when that agent finishes the current talk interval. The comparison therefore demonstrates setup overlap under the stated assumption; it is not a claim that the current SQLite allocator supports future-slot reservations in production.

### Provider B with 3 second latency

| Mode | Attempts | Connected | Failed | Timeouts | Predials | Setup seconds saved | Utilization | Max connected | Agentless |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Progressive | 296 | 137 | 159 | 33 | 0 | 0 | 85.6% | 8 | 0 |
| Predictive | 316 | 147 | 169 | 33 | 140 | 464 | 91.9% | 8 | 0 |

### Provider B outage after 900 seconds

Same inputs as above, with `--outage-after-seconds 900`:

| Mode | Attempts | Connected | Failed | Timeouts | Predials | Setup seconds saved | Utilization | Max connected | Agentless |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Progressive | 161 | 71 | 90 | 21 | 0 | 0 | 44.4% | 8 | 0 |
| Predictive | 170 | 74 | 96 | 21 | 67 | 208 | 46.2% | 8 | 0 |

Both runs stop starting calls when the outage threshold is reached. Calls already started before the threshold can finish their simulated setup and resolve; predictive may therefore have more attempts in flight at the cutoff.

### Low and high answer-rate cases with 3 second provider latency

Inputs: 8 agents, 500 borrowers, 1,800 second horizon, Provider A, seed 7. Scenario A is 20% answers / 120 second talk time; Scenario C is 70% / 180 seconds.

| Scenario | Mode | Attempts | Connected | Failed | Predials | Setup seconds saved | Utilization | Max connected | Agentless |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | Progressive | 451 | 89 | 362 | 0 | 0 | 74.2% | 8 | 0 |
| A | Predictive | 461 | 92 | 369 | 87 | 144 | 76.7% | 8 | 0 |
| C | Progressive | 111 | 80 | 31 | 0 | 0 | 100.0% | 8 | 0 |
| C | Predictive | 111 | 80 | 31 | 72 | 384 | 100.0% | 8 | 0 |

In the low-answer scenario, setup overlap improves modeled utilization by 2.5 percentage points and connects three more calls within the horizon. In Scenario C, talk-time demand already saturates all agents in both modes; predictive overlaps setup but cannot raise utilization above 100% or increase completions in this finite campaign window.

## Provider B stress run

Scenario B, 8 agents, 50 borrowers, seed 17:

| Mode | Attempts | Connected | Failed | Timeouts | Duplicate events | Stale/out-of-order events | Safety reductions |
|---|---:|---:|---:|---:|---:|---:|---:|
| Progressive | 107 | 50 | 57 | 10 | 97 | 50 | 0 |
| Predictive | 107 | 50 | 57 | 10 | 97 | 50 | 12 |

Provider B outcomes are reproducible at the seed. Duplicate event IDs were ignored. The intentionally early `ANSWERED` callback was accepted, the subsequent stale `RINGING` callback ignored, and terminal completion stayed terminal.

## Forced outage

`simulate --mode predictive --scenario D --provider a --outage-tick 3 --compact` stopped new initiations after the forced outage. With 8 agents and 50 borrowers, it recorded 24 attempts, 15 connected calls, 9 failures, and 35 queued borrowers remaining. Safety rejected later requests while provider health was false.

## Basic local load measurement

`load-test --agents 100 --borrowers 1000 --calls 100` initiated all 100 requested calls. Two local runs measured 1,374.6 and 2,536.7 reservation+initiation operations per second; this is a small, machine-dependent SQLite baseline, not a throughput guarantee. The load harness deliberately leaves calls active to exercise allocation limits.

## Interpretation and caveats

The regular simulator applies provider callbacks synchronously and is useful for state and safety decisions. The timed mode models each agent's setup and talk intervals, including one future reserved slot per agent, but does not run a global provider-event queue or model callback network jitter. Provider B's timeouts/duplicates/out-of-order behavior is exercised in the regular simulator; timed mode models provider failure probability and optional fixed latency. A production implementation still needs durable future-slot reservations and provider reconciliation before it may pre-dial on an agent's behalf.
