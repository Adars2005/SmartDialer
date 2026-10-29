"""Independent final authority for call initiation."""
from dataclasses import dataclass


@dataclass
class SafetyDecision:
    requested: int
    approved: int
    action: str
    reason: str
    snapshot: dict


class SafetyController:
    def __init__(self, store, max_inflight=None):
        self.store = store
        self.max_inflight = max_inflight

    def evaluate(self, requested: int, provider_healthy=True):
        snapshot = self.store.snapshot()
        free = snapshot["free_agents"]
        queued = snapshot["queued_borrowers"]
        limit = min(free, queued)
        if self.max_inflight is not None:
            limit = min(limit, max(0, self.max_inflight - snapshot["active_calls"]))
        if not provider_healthy:
            decision = SafetyDecision(requested, 0, "reject", "provider unhealthy; stop new initiations", snapshot)
        else:
            approved = max(0, min(int(requested), limit))
            action = "approve" if approved == requested else ("reduce" if approved else "reject")
            reason = "within live agent/borrower capacity" if approved == requested else "clamped to live capacity"
            decision = SafetyDecision(requested, approved, action, reason, snapshot)
        self.store.record_decision(decision.requested, decision.approved, decision.action, decision.reason, decision.snapshot)
        return decision

