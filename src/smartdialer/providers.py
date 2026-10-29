"""Provider abstraction and intentionally different deterministic mock adapters."""
from dataclasses import dataclass
import random
import time
import uuid


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    call_id: str
    event_type: str


class ProviderError(RuntimeError):
    pass


class MockProvider:
    def __init__(self, name, answer_rate=.5, failure_rate=0, latency=0, seed=1, duplicates=False,
                 out_of_order=False, failure_threshold=3):
        self.name = name
        self.answer_rate = answer_rate
        self.failure_rate = failure_rate
        self.latency = latency
        self.rng = random.Random(seed)
        self.duplicates = duplicates
        self.out_of_order = out_of_order
        self.healthy = True
        self.timeouts = 0
        self.consecutive_timeouts = 0
        self.failure_threshold = max(1, failure_threshold)

    def initiate(self, call_id):
        if self.latency:
            time.sleep(self.latency)
        if not self.healthy or self.rng.random() < self.failure_rate:
            self.timeouts += 1
            self.consecutive_timeouts += 1
            if self.consecutive_timeouts >= self.failure_threshold:
                self.healthy = False
            raise ProviderError(f"{self.name}: simulated timeout/outage")
        self.consecutive_timeouts = 0
        if self.rng.random() >= self.answer_rate:
            kinds = ["RINGING", "FAILED"]
        else:
            kinds = ["RINGING", "ANSWERED", "CONNECTED", "COMPLETED"]
        if self.out_of_order and len(kinds) > 2:
            # Deliver ANSWERED before RINGING while preserving terminal completion last.
            kinds = [kinds[1], kinds[0], *kinds[2:]]
        events = [ProviderEvent(str(uuid.uuid4()), call_id, kind) for kind in kinds]
        if self.duplicates and events:
            events.insert(1, events[0])
        return events

    def recover(self):
        """Manual half-open success hook for deterministic mock/provider operations."""
        self.healthy = True
        self.consecutive_timeouts = 0


def provider_a(answer_rate=.5, seed=11):
    return MockProvider("provider-a", answer_rate, failure_rate=.01, latency=0, seed=seed)


def provider_b(answer_rate=.5, seed=29):
    return MockProvider("provider-b", answer_rate, failure_rate=.12, latency=0, seed=seed,
                        duplicates=True, out_of_order=True)

