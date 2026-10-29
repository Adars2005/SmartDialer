import unittest
import tempfile
import threading
from pathlib import Path

from smartdialer.providers import MockProvider, ProviderError
from smartdialer.safety import SafetyController
from smartdialer.store import Store


class SafetyEventTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.add_agent("a")
        self.store.add_borrower("b")

    def test_safety_clamps_to_live_agent_capacity(self):
        decision = SafetyController(self.store).evaluate(10)
        self.assertEqual((decision.approved, decision.action), (1, "reduce"))
        call = self.store.reserve("p")
        self.assertTrue(self.store.initiate(call))
        self.assertEqual(SafetyController(self.store).evaluate(1).approved, 0)

    def test_provider_health_stops_new_calls(self):
        decision = SafetyController(self.store).evaluate(1, provider_healthy=False)
        self.assertEqual(decision.approved, 0)

    def test_provider_opens_circuit_after_consecutive_timeouts(self):
        provider = MockProvider("flaky", failure_rate=1, failure_threshold=2)
        for call_id in ("one", "two"):
            with self.assertRaises(ProviderError):
                provider.initiate(call_id)
        self.assertFalse(provider.healthy)

    def test_duplicate_and_out_of_order_events_are_safe(self):
        call = self.store.reserve("p")
        self.store.initiate(call)
        self.assertEqual(self.store.apply_event(call, "e1", "RINGING"), "applied")
        self.assertEqual(self.store.apply_event(call, "e2", "COMPLETED"), "applied")
        self.assertEqual(self.store.apply_event(call, "e3", "RINGING"), "ignored_stale")
        self.assertEqual(self.store.apply_event(call, "e2", "COMPLETED"), "duplicate")
        self.assertEqual(self.store.snapshot()["calls"], {"COMPLETED": 1})

    def test_worker_crash_reservation_recovery(self):
        call = self.store.reserve("p", lease_seconds=-1)
        self.assertEqual(self.store.recover_expired(), 1)
        self.assertEqual(self.store.snapshot()["free_agents"], 1)
        self.assertEqual(self.store.snapshot()["queued_borrowers"], 1)

    def test_worker_crash_after_answered_holds_resources_until_provider_result(self):
        call = self.store.reserve("p")
        self.store.initiate(call)
        self.assertEqual(self.store.apply_event(call, "answered", "ANSWERED"), "applied")
        with self.store.tx() as db:
            db.execute("UPDATE calls SET lease_until=0 WHERE id=?", (call,))
        self.assertEqual(self.store.recover_expired(), 1)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["calls"], {"RECONCILING": 1})
        self.assertEqual(snapshot["agents"], {"CONNECTED": 1})
        self.assertEqual(snapshot["borrowers"], {"HOLD": 1})
        self.assertEqual(snapshot["queued_borrowers"], 0)
        self.assertEqual(self.store.apply_event(call, "completed", "COMPLETED"), "applied")
        self.assertEqual(self.store.snapshot()["calls"], {"COMPLETED": 1})
        self.assertEqual(self.store.snapshot()["borrowers"], {"DONE": 1})

    def test_confirmed_provider_not_found_releases_reconciled_borrower(self):
        call = self.store.reserve("p")
        self.store.initiate(call)
        with self.store.tx() as db:
            db.execute("UPDATE calls SET lease_until=0 WHERE id=?", (call,))
        self.store.recover_expired()
        self.assertTrue(self.store.resolve_reconciliation(call, "not_found"))
        self.assertEqual(self.store.snapshot()["calls"], {"FAILED": 1})
        self.assertEqual(self.store.snapshot()["queued_borrowers"], 1)

    def test_agent_disappearing_during_setup_cancels_call_and_releases_borrower(self):
        call = self.store.reserve("p")
        self.store.set_agent_state("a", "OFFLINE")
        self.assertFalse(self.store.initiate(call))
        self.assertEqual(self.store.snapshot()["calls"], {"FAILED": 1})
        self.assertEqual(self.store.snapshot()["queued_borrowers"], 1)
        self.assertEqual(self.store.snapshot()["free_agents"], 0)

    def test_agent_state_rejects_invalid_transition(self):
        self.assertEqual(self.store.set_agent_state("a", "CONNECTED"), 0)
        self.assertEqual(self.store.snapshot()["free_agents"], 1)

    def test_failed_calls_release_agent(self):
        call = self.store.reserve("p")
        self.store.initiate(call)
        self.store.fail_initiation(call, "timeout")
        self.store.release_wrap_up()
        self.assertEqual(self.store.snapshot()["free_agents"], 1)

    def test_two_workers_cannot_reserve_the_same_agent(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            store = Store(str(Path(directory) / "race.db"))
            store.add_agent("shared-agent")
            store.add_borrower("borrower-a")
            store.add_borrower("borrower-b")
            barrier = threading.Barrier(3)
            results = []
            def reserve():
                barrier.wait()
                results.append(store.reserve("provider"))
            workers = [threading.Thread(target=reserve) for _ in range(2)]
            for worker in workers: worker.start()
            barrier.wait()
            for worker in workers: worker.join()
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(store.snapshot()["active_calls"], 1)


if __name__ == "__main__":
    unittest.main()

