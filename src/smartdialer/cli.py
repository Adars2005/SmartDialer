"""Command-line entry points for demos and local measurement."""
import argparse
import json

from .simulation import SCENARIOS, run_load_test, run_simulation, run_timed_simulation


def main():
    parser = argparse.ArgumentParser(prog="smartdialer")
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate", help="run one deterministic scenario")
    sim.add_argument("--mode", choices=["progressive", "predictive"], default="predictive")
    sim.add_argument("--scenario", choices=SCENARIOS, default="B")
    sim.add_argument("--provider", choices=["a", "b"], default="a")
    sim.add_argument("--agents", type=int, default=8)
    sim.add_argument("--borrowers", type=int, default=50)
    sim.add_argument("--seed", type=int, default=7)
    sim.add_argument("--outage-tick", type=int, help="force provider outage at this zero-based simulation tick")
    sim.add_argument("--timed", action="store_true", help="use the time-stepped setup-overlap simulator")
    sim.add_argument("--horizon-seconds", type=int, default=1800, help="timed simulation horizon (default: 1800)")
    sim.add_argument("--setup-seconds", type=int, default=5, help="mock call setup time (default: 5)")
    sim.add_argument("--provider-latency", type=int, default=0, help="extra provider latency seconds")
    sim.add_argument("--outage-after-seconds", type=int, help="stop new timed attempts at this second")
    sim.add_argument("--compact", action="store_true", help="omit per-tick safety decision detail")
    load = sub.add_parser("load-test", help="measure local SQLite reservation throughput")
    load.add_argument("--agents", type=int, default=1000)
    load.add_argument("--borrowers", type=int, default=10000)
    load.add_argument("--calls", type=int, default=1000)
    args = parser.parse_args()
    if args.command == "simulate":
        if args.timed:
            result = run_timed_simulation(args.agents, args.borrowers, args.mode, args.scenario, args.provider,
                                          args.seed, args.horizon_seconds, args.setup_seconds,
                                          args.provider_latency, args.outage_after_seconds)
        else:
            result = run_simulation(args.agents, args.borrowers, args.mode, args.scenario, args.provider, args.seed,
                                    outage_tick=args.outage_tick)
        if args.compact:
            result.pop("safety_decisions", None)
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(run_load_test(args.agents, args.borrowers, args.calls), indent=2))


if __name__ == "__main__":
    main()

