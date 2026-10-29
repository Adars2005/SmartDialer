import unittest

from smartdialer.simulation import run_simulation, run_timed_simulation


class SimulationTests(unittest.TestCase):
    def test_seeded_runs_are_reproducible_in_key_metrics(self):
        first = run_simulation(4, 10, "predictive", "B", "a", seed=3)
        second = run_simulation(4, 10, "predictive", "B", "a", seed=3)
        for key in ("attempts", "connected", "failed", "events", "agent_utilization_pct"):
            self.assertEqual(first[key], second[key])

    def test_provider_b_exercises_duplicate_and_reordered_events(self):
        result = run_simulation(4, 12, "progressive", "B", "b", seed=8)
        self.assertGreater(result["events"], result["attempts"])
        self.assertGreater(result["duplicate_events"] + result["stale_events"], 0)
        self.assertLessEqual(result["attempts"], 4 * 20)

    def test_outage_stops_later_initiations(self):
        result = run_simulation(2, 10, "predictive", "B", "a", seed=4, max_ticks=5, outage_tick=1)
        self.assertTrue(result["provider_outage"])
        self.assertTrue(any(d["action"] == "reject" for d in result["safety_decisions"]))

    def test_timed_predictive_overlaps_setup_without_agentless_connections(self):
        progressive = run_timed_simulation(4, 300, "progressive", "B", "a", seed=11, horizon_seconds=1800)
        predictive = run_timed_simulation(4, 300, "predictive", "B", "a", seed=11, horizon_seconds=1800)
        self.assertGreater(predictive["predial_calls"], 0)
        self.assertGreater(predictive["setup_seconds_saved"], 0)
        self.assertGreaterEqual(predictive["agent_utilization_pct"], progressive["agent_utilization_pct"])
        self.assertEqual(predictive["connected_without_agent"], 0)
        self.assertLessEqual(predictive["max_connected_calls"], 4)

    def test_timed_forced_outage_blocks_new_calls_after_time(self):
        result = run_timed_simulation(2, 100, "predictive", "B", "a", seed=1,
                                      horizon_seconds=600, outage_after_seconds=120)
        self.assertTrue(result["provider_outage"])
        self.assertLess(result["attempts"], 100)


if __name__ == "__main__":
    unittest.main()

