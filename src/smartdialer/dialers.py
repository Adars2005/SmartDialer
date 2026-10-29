"""Progressive policy and predictive pacing request generator."""
from dataclasses import dataclass


@dataclass
class PaceProposal:
    calls: int
    explanation: dict


class ProgressiveDialer:
    def propose(self, snapshot):
        return PaceProposal(snapshot["free_agents"], {"mode": "progressive", "free_agents": snapshot["free_agents"]})


class PredictivePacer:
    """Requests speculative setup calls; has no provider dependency by design."""
    def __init__(self, answer_rate=.5, setup_seconds=5, talk_seconds=90, aggressiveness=1.0, max_extra=10):
        self.answer_rate = min(.95, max(.05, answer_rate))
        self.setup_seconds = max(1, setup_seconds)
        self.talk_seconds = max(1, talk_seconds)
        self.aggressiveness = max(0.0, aggressiveness)
        self.max_extra = max(0, max_extra)

    def propose(self, snapshot, provider_healthy=True):
        free = snapshot["free_agents"]
        queued = snapshot["queued_borrowers"]
        pending = snapshot["active_calls"]
        if not provider_healthy or free <= 0:
            request = 0
            fallback = "provider unhealthy or no free agents; pause/fallback to progressive"
        else:
            expected_need = max(0, round(free * (1 / self.answer_rate - 1) * self.aggressiveness))
            setup_factor = min(2.0, self.talk_seconds / self.setup_seconds)
            extra = min(self.max_extra, round(expected_need * min(1.0, setup_factor / 10)))
            request = min(queued, free + extra)
            fallback = None
        return PaceProposal(request, {"mode": "predictive", "answer_rate": self.answer_rate,
                                      "setup_seconds": self.setup_seconds, "talk_seconds": self.talk_seconds,
                                      "free_agents": free, "pending_calls": pending,
                                      "expected_extra_need": expected_need if provider_healthy and free > 0 else 0,
                                      "request": request, "fallback": fallback})

