# SmartDialer prototype

A local Python prototype demonstrating progressive dialing, predictive pacing behind an independent safety boundary, atomic allocation, mock providers, and reproducible failure scenarios. It uses SQLite and the Python standard library; no external service or real telecom account is required.

## Run it (Windows PowerShell)

Requirements: Python 3.11 or newer and the Windows Python launcher (`py`). Open PowerShell in the project root (`SmartDialer`) and run these commands. Python 3.15 was used for the final local verification. Activation is optional, so this works when PowerShell script execution is disabled:

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

To reproduce the documented seeded Provider B scenario, add `--seed 17` to the simulation command. Simulations without an explicit seed use the default seed and can produce results different from the tables in `docs/simulation-results.md`.

Run a simulation:

```powershell
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --mode progressive --scenario A --provider a
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --mode predictive --scenario C --provider b --compact
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --mode progressive --scenario B --provider a --timed --agents 8 --borrowers 500 --horizon-seconds 1800
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --mode predictive --scenario B --provider a --timed --agents 8 --borrowers 500 --horizon-seconds 1800
```

Run the changing-conditions scenario with a forced provider outage, the tests, and a basic load test:

```powershell
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --mode predictive --scenario D --provider a --outage-tick 3 --compact
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m smartdialer.cli load-test --agents 100 --borrowers 1000 --calls 100
```

If you don't want to install the package, use Python from the repository root instead:

```powershell
$env:PYTHONPATH = "src"
py -3 -m smartdialer.cli simulate --mode progressive --scenario A --provider a
py -3 -m smartdialer.cli simulate --mode predictive --scenario C --provider b --compact
py -3 -m smartdialer.cli simulate --mode predictive --scenario D --provider a --outage-tick 3 --compact
py -3 -m unittest discover -s tests -v
py -3 -m smartdialer.cli load-test --agents 100 --borrowers 1000 --calls 100
```

To see all options for either command:

```powershell
.\.venv\Scripts\python.exe -m smartdialer.cli simulate --help
.\.venv\Scripts\python.exe -m smartdialer.cli load-test --help
```

Scenario A is 20% answer rate / 120 second talk time; B is 50% / 90 seconds; C is 70% / 180 seconds; D changes answer rate and agent availability during the run. Use `--seed` to reproduce provider outcomes. `--outage-tick` forces an outage at a zero-based tick. Provider A is fast/reliable; provider B injects timeouts, duplicate events, and out-of-order events.

Add `--timed` to compare setup overlap over simulated time. The time model reserves a specific agent's next slot before predictive pre-dialing; the answer event occurs only when that agent is free. `--horizon-seconds`, `--setup-seconds`, `--provider-latency`, and `--outage-after-seconds` tune the timed run. The regular simulator remains useful for inspecting individual pacing and Safety Controller decisions.

## Design overview

See [docs/brief.md](docs/brief.md) for the diagram and folder layout, [docs/architecture.md](docs/architecture.md) for component responsibilities, [docs/state-machines.md](docs/state-machines.md) for lifecycle rules, [docs/adr-001-local-store-and-safety-boundary.md](docs/adr-001-local-store-and-safety-boundary.md) for key tradeoffs, [docs/simulation-results.md](docs/simulation-results.md) for measured scenarios, [docs/assignment-validation.md](docs/assignment-validation.md) for the requirement-by-requirement review, and [docs/interview-notes.md](docs/interview-notes.md) for a technical walkthrough.

The Safety Controller is the only path from a pacing request to the allocator. It clamps every request to free agents, queued borrowers, and the configured in-flight limit. The allocator commits agent and borrower reservations before initiation. Predictive pacing cannot invoke providers.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover capacity enforcement, outage rejection, concurrent reservations, duplicate/out-of-order events, post-answer crash reconciliation, provider circuit opening, expired lease recovery, and reproducible simulation. The load command is a local measurement, not a production performance claim.

## Prototype limitations

The regular simulator applies callbacks synchronously. The timed mode models per-agent talk intervals and setup overlap, but it is still a deterministic simulation rather than a live event loop; it reserves one future slot per agent and schedules an answer only at that agent's availability boundary. Both modes abstract authenticated webhooks and distributed clocks. Expired uncertain calls stay in `RECONCILING` until a provider event or lookup resolves them. The mock circuit opens after three consecutive timeouts; automatic half-open recovery and production retry backoff are not implemented. SQLite is intended for one-host demonstration, not a high-write distributed deployment.

## Assignment's final design question

Use predictive statistics to choose borrowers and prepare bounded call requests, but reserve one specific agent and borrower before any provider initiation. Keep every request behind a controller that reads authoritative live state, provider health, and current call reservations. If capacity is uncertain, reduce to progressive dialing or pause. This preserves deterministic one-agent-per-active-call safety while using forecasted answer rates to reduce avoidable setup delay. The timed prototype models the gain from overlapping setup with an agent's current talk interval while reserving that agent's next slot.
