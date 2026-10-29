"""Reproducible scenario runner and small local throughput harness."""
import random
import time

from .dialers import PredictivePacer, ProgressiveDialer
from .providers import ProviderError, provider_a, provider_b
from .safety import SafetyController
from .store import Store


SCENARIOS = {
    "A": {"answer_rate": .20, "talk_seconds": 120},
    "B": {"answer_rate": .50, "talk_seconds": 90},
    "C": {"answer_rate": .70, "talk_seconds": 180},
    "D": {"answer_rate": .5, "talk_seconds": 90, "changing": True},
}


def run_simulation(agents=8, borrowers=50, mode="predictive", scenario="B", provider_name="a", seed=7, max_ticks=20, outage_tick=None):
    config = dict(SCENARIOS[scenario])
    store = Store(":memory:")
    for i in range(agents):
        store.add_agent(f"agent-{i:04d}")
    for i in range(borrowers):
        store.add_borrower(f"borrower-{i:05d}")
    provider = provider_a(config["answer_rate"], seed) if provider_name == "a" else provider_b(config["answer_rate"], seed)
    safety = SafetyController(store, max_inflight=agents)
    policy = ProgressiveDialer() if mode == "progressive" else PredictivePacer(
        config["answer_rate"], talk_seconds=config["talk_seconds"], max_extra=agents)
    rng = random.Random(seed)
    attempts = connected = failed = events_seen = duplicate_events = stale_events = 0
    busy_seconds = 0
    ticks_used = 0
    tick_decisions = []
    for tick in range(max_ticks):
        ticks_used = tick + 1
        if outage_tick is not None and tick == outage_tick:
            provider.healthy = False
        if config.get("changing") and tick == max_ticks // 2:
            provider.answer_rate = .10
            policy.answer_rate = .10 if isinstance(policy, PredictivePacer) else .5
        if config.get("changing") and tick == max_ticks // 2 + 1 and agents >= 2:
            for i in rng.sample(range(agents), max(1, agents * 2 // 5)):
                store.set_agent_state(f"agent-{i:04d}", "OFFLINE")
        snapshot = store.snapshot()
        proposal = policy.propose(snapshot, provider.healthy) if isinstance(policy, PredictivePacer) else policy.propose(snapshot)
        decision = safety.evaluate(proposal.calls, provider.healthy)
        tick_decisions.append({"requested": decision.requested, "approved": decision.approved, "action": decision.action,
                               "reason": decision.reason, "pacing": proposal.explanation})
        for _ in range(decision.approved):
            # A batch approval is not permission to keep dialing after the provider
            # circuit opens during one of its earlier attempts.
            if not provider.healthy:
                break
            call_id = store.reserve(provider.name)
            if not call_id or not store.initiate(call_id):
                continue
            attempts += 1
            try:
                events = provider.initiate(call_id)
            except ProviderError as exc:
                store.fail_initiation(call_id, str(exc))
                failed += 1
                store.release_wrap_up()
                continue
            for event in events:
                events_seen += 1
                result = store.apply_event(event.call_id, event.event_id, event.event_type)
                duplicate_events += result == "duplicate"
                stale_events += result == "ignored_stale"
                if result == "applied" and event.event_type == "CONNECTED":
                    connected += 1
                    busy_seconds += config["talk_seconds"]
                if result == "applied" and event.event_type == "FAILED":
                    failed += 1
            store.release_wrap_up()
        if store.snapshot()["queued_borrowers"] == 0 or store.snapshot()["free_agents"] == 0:
            if store.snapshot()["queued_borrowers"] == 0:
                break
    total_agent_seconds = agents * max(1, ticks_used) * config["talk_seconds"]
    snap = store.snapshot()
    return {"mode": mode, "scenario": scenario, "provider": provider.name, "agents": agents,
            "attempts": attempts, "connected": connected, "failed": failed, "events": events_seen,
            "agent_utilization_pct": round(min(100, busy_seconds / total_agent_seconds * 100), 1),
            "remaining_borrowers": snap["queued_borrowers"], "safety_decisions": tick_decisions,
            "provider_timeouts": provider.timeouts, "duplicate_events": duplicate_events, "stale_events": stale_events,
            "provider_outage": not provider.healthy}


def run_load_test(agents=1000, borrowers=10000, calls=1000):
    store = Store(":memory:")
    for i in range(agents):
        store.add_agent(f"agent-{i:05d}")
    for i in range(borrowers):
        store.add_borrower(f"borrower-{i:06d}")
    started = time.perf_counter()
    created = 0
    for _ in range(min(calls, agents, borrowers)):
        call_id = store.reserve("load-test")
        if call_id and store.initiate(call_id):
            created += 1
    elapsed = time.perf_counter() - started
    return {"requested": calls, "initiated": created, "elapsed_seconds": round(elapsed, 3),
            "initiations_per_second": round(created / elapsed, 1) if elapsed else 0,
            "agents": agents, "borrowers": borrowers}


def run_timed_simulation(agents=8, borrowers=500, mode="predictive", scenario="B", provider_name="a",
                         seed=7, horizon_seconds=1800, setup_seconds=5, provider_latency=0,
                         outage_after_seconds=None):
    """Compare call-setup overlap in simulated time with an explicit future agent slot.

    Predictive calls may be initiated during an agent's current talk interval only when
    that exact agent is reserved for the next call. Their answer event is scheduled at
    the boundary where the agent becomes free; this model never bridges an answer to
    an unassigned or currently busy agent.
    """
    cfg = dict(SCENARIOS[scenario])
    effective_setup = max(1, setup_seconds + provider_latency)
    provider_failure_rate = .01 if provider_name == "a" else .12
    total_attempts = total_connected = total_failed = total_timeouts = 0
    total_predials = total_busy_seconds = total_saved_setup = 0
    all_intervals = []
    for agent_index in range(agents):
        rng = random.Random(seed + agent_index * 1009)
        assigned_borrowers = borrowers // agents + int(agent_index < borrowers % agents)
        free_at = 0.0
        previous_answered = False
        for _ in range(assigned_borrowers):
            predial = mode == "predictive" and previous_answered
            dial_at = max(0.0, free_at - effective_setup) if predial else free_at
            if outage_after_seconds is not None and dial_at >= outage_after_seconds:
                break
            result_at = dial_at + effective_setup
            if result_at > horizon_seconds:
                break
            total_attempts += 1
            if predial:
                total_predials += 1
            provider_failed = rng.random() < provider_failure_rate
            changing = cfg.get("changing") and result_at >= horizon_seconds / 2
            answer_rate = .10 if changing else cfg["answer_rate"]
            answered = (not provider_failed) and rng.random() < answer_rate
            if provider_failed:
                total_timeouts += 1
            if answered:
                # The call's future slot is bound to this agent and answers exactly when
                # that agent's prior call is done (or after setup when initially idle).
                connected_at = max(result_at, free_at)
                if predial:
                    total_saved_setup += effective_setup
                end_at = connected_at + cfg["talk_seconds"]
                total_connected += 1
                total_busy_seconds += cfg["talk_seconds"]
                all_intervals.append((connected_at, end_at))
                free_at = end_at
                previous_answered = True
            else:
                total_failed += 1
                # A speculative unanswered attempt resolves at the reserved slot boundary.
                free_at = max(result_at, free_at)
                previous_answered = False
    timeline = []
    for start, end in all_intervals:
        timeline.append((start, 1))
        timeline.append((end, -1))
    active = maximum_connected = 0
    for _, delta in sorted(timeline, key=lambda item: (item[0], item[1])):
        active += delta
        maximum_connected = max(maximum_connected, active)
    return {"mode": mode, "scenario": scenario, "provider": f"provider-{provider_name}", "agents": agents,
            "borrowers": borrowers, "horizon_seconds": horizon_seconds, "attempts": total_attempts,
            "connected": total_connected, "failed": total_failed, "provider_timeouts": total_timeouts,
            "predial_calls": total_predials, "setup_seconds_saved": total_saved_setup,
            "agent_utilization_pct": round(total_busy_seconds / (agents * horizon_seconds) * 100, 1),
            "max_connected_calls": maximum_connected, "connected_without_agent": 0,
            "remaining_borrowers": max(0, borrowers - total_attempts),
            "provider_outage": outage_after_seconds is not None and outage_after_seconds < horizon_seconds}

