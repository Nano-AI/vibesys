"""Engine-agnostic benchmark harness (TASKS.md 1.7): throughput + latency.

Reports the two performance axes of the thesis (accuracy is graded separately by
checker.py):

  * THROUGHPUT  -- events processed per second (the engine's intrinsic ceiling on this
    workload). Emitted as the `Primary metric:` line the vibe-database Perf Evaluator reads,
    and as `events_per_sec` in the --output-json file.
  * LATENCY     -- per-snapshot maintain-step time percentiles (p50/p95/p99).

Two runner shapes share one reporting path so Flink/RisingWave/Rust are measured the same:

  * PythonMaintainerRunner -- an in-process Python maintainer (the reference / bespoke
    stand-in in Phase 1). Real numbers now.
  * SubprocessRunner       -- a compiled engine (Phase 2 Rust binary) invoked as a process
    that reads the events file and writes a results file; throughput = events / wall-time.

NOTE ON LATENCY DEFINITION: in-process we measure per-snapshot COMPUTE latency (a proxy).
True end-to-end event->output latency for the LIVE Kafka-fed engines (Flink, RisingWave)
is marker-based (distinctive events timed from ingest to result arrival) -- that rides the
load driver + results topic (CONTRACT.md §7, TASKS.md 1.8) and is reserved for the live
runs; it plugs into this same reporting path.
"""

import argparse
import json
import os
import statistics  # noqa: F401  (kept for downstream latency stats; harmless)
import subprocess
import sys
import time

# Shared truth (config, queries, harness helpers) lives in the reference slot's core/.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "core")
)

import config  # noqa: E402
import queries  # noqa: E402
from harness import load_events, snapshots  # noqa: E402


def _percentiles(xs, ps=(50, 95, 99)):
    if not xs:
        return {p: 0.0 for p in ps}
    s = sorted(xs)
    out = {}
    for p in ps:
        # nearest-rank
        idx = min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))
        out[p] = s[idx]
    return out


class PythonMaintainerRunner:
    """Benchmark an in-process Python maintainer for one query."""

    def __init__(self, query):
        self.q = queries.get(query) if isinstance(query, str) else query

    def measure(self, events, snaps):
        latencies = []  # per-snapshot compute time (s)
        gen = self.q.maintainer().run(events, snaps)
        t0 = time.perf_counter()
        prev = t0
        for _ in gen:  # each item = one settled snapshot
            now = time.perf_counter()
            latencies.append(now - prev)
            prev = now
        wall = time.perf_counter() - t0
        return {
            "engine": "python-ref",
            "query": self.q.name,
            "events": len(events),
            "snapshots": len(snaps),
            "wall_s": wall,
            "throughput_eps": len(events) / wall if wall > 0 else float("inf"),
            "snap_latency_ms": {p: v * 1e3 for p, v in _percentiles(latencies).items()},
        }


class SubprocessRunner:
    """Benchmark a compiled engine (Phase 2). The engine reads `events_csv` and writes a
    results file per the I/O contract; we time the whole process. `cmd` is a list; the
    events path and output path are substituted for {events} and {out}."""

    def __init__(self, query, cmd):
        self.q = queries.get(query) if isinstance(query, str) else query
        self.cmd = cmd

    def measure(self, events, snaps, events_csv=None, out_path="engine_out.jsonl"):
        events_csv = events_csv or config.EVENTS_CSV
        argv = [a.format(events=events_csv, out=out_path) for a in self.cmd]
        t0 = time.perf_counter()
        subprocess.run(argv, check=True)
        wall = time.perf_counter() - t0
        return {
            "engine": argv[0],
            "query": self.q.name,
            "events": len(events),
            "snapshots": len(snaps),
            "wall_s": wall,
            "throughput_eps": len(events) / wall if wall > 0 else float("inf"),
            "snap_latency_ms": None,  # needs marker protocol (reserved)
            "out_path": out_path,
        }


def benchmark(query, repeat=5):
    """Run the in-process reference maintainer `repeat` times; report the median run."""
    events = load_events(config.EVENTS_CSV)
    snaps = snapshots()
    runner = PythonMaintainerRunner(query)
    runs = [runner.measure(events, snaps) for _ in range(repeat)]
    runs.sort(key=lambda r: r["throughput_eps"])
    return runs[len(runs) // 2]  # median by throughput


def _print(m):
    print("=" * 60)
    print(f"BENCHMARK  engine={m['engine']}  query={m['query']}")
    print("-" * 60)
    print(f"  events / snapshots : {m['events']} / {m['snapshots']}")
    print(f"  wall time          : {m['wall_s'] * 1e3:.2f} ms")
    if m["snap_latency_ms"]:
        lat = m["snap_latency_ms"]
        print(
            f"  snapshot latency   : p50 {lat[50]:.3f} ms  "
            f"p95 {lat[95]:.3f} ms  p99 {lat[99]:.3f} ms"
        )
    print("-" * 60)
    # The vibe-database Perf Evaluator reads this line; `events_per_sec` names the JSON field
    # (OBJECTIVE.md declares it the Headline metric).
    print(f"Primary metric: events_per_sec = {m['throughput_eps']:.0f}")
    print("=" * 60)


def _output_json(m):
    """The machine-readable record the Perf Evaluator parses (headline field = events_per_sec)."""
    lat = m.get("snap_latency_ms")
    return {
        "events_per_sec": m["throughput_eps"],  # <-- headline metric
        "engine": m["engine"],
        "query": m["query"],
        "events": m["events"],
        "snapshots": m["snapshots"],
        "wall_s": m["wall_s"],
        "p50_ms": lat[50] if lat else None,
        "p95_ms": lat[95] if lat else None,
        "p99_ms": lat[99] if lat else None,
    }


def main():
    ap = argparse.ArgumentParser(description="Throughput + latency benchmark.")
    ap.add_argument("--query", default="metering", choices=queries.ALL)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument(
        "--output-json",
        default=None,
        help="write the metric record (with events_per_sec) to this path",
    )
    args = ap.parse_args()
    m = benchmark(args.query, repeat=args.repeat)
    _print(m)
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(_output_json(m), f, indent=2)
        print(f"wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
