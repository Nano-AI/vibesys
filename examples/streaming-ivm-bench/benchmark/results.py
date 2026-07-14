"""Results collection (TASKS.md 1.12): one table of {engine x query x metric}.

Runs every engine that can run here (the Python reference maintainer -- the Phase-1
"bespoke" stand-in) across every query, grading accuracy against the oracle and measuring
throughput + latency with the same code every other engine uses (accuracy.py, benchmark.py).
Flink and RisingWave rows are emitted as PENDING placeholders until their infra is up
(docker compose; baselines/*/README.md) -- then their `results.<query>` dumps are graded by
checker.py and pasted in via `add_engine_from_file`.

Writes results.md (human) + results.json (machine) to the current directory -- run this from
the example root so they land next to baselines/. This is the first real bespoke-vs-general
scaffold; the synthesized Rust engine slots into the same table in Phase 3.

Usage (from the example root):
  python3 benchmark/results.py                                 # reference rows + pending placeholders
  python3 benchmark/results.py --add flink metering f.jsonl    # merge a graded live-engine dump
"""

import argparse
import json
import os
import sys

# This tool spans all three slots: the reference core (config/queries/harness) AND the
# accuracy_checker (accuracy/checker). It is an operator tool (not part of the agent loop),
# run standalone from the example root, so both sibling slots are importable by path.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "reference", "core"))
sys.path.insert(0, os.path.join(_HERE, "..", "accuracy_checker"))

import accuracy  # noqa: E402  (accuracy_checker/)
import benchmark  # noqa: E402  (local to benchmark/)
import checker  # noqa: E402  (accuracy_checker/)
import config  # noqa: E402  (reference/core/)
import queries  # noqa: E402  (reference/core/)
from harness import load_events, oracle_results, snapshots  # noqa: E402  (reference/core/)

_PENDING = "pending (docker; see baselines/*/README.md)"


def reference_rows():
    """Grade + benchmark the Python reference maintainer for every query."""
    events = load_events(config.EVENTS_CSV)
    snaps = snapshots()
    rows = []
    for name in queries.ALL:
        q = queries.get(name)
        truth = oracle_results(q, snaps)
        maint = list(q.maintainer().run(events, snaps))
        acc = accuracy.score(truth, maint)
        perf = benchmark.benchmark(name, repeat=5)
        rows.append(
            {
                "engine": "python-ref (bespoke stand-in)",
                "query": name,
                "exact_match_rate": acc["exact_match_rate"],
                "f1": acc["f1"],
                "value_mae": acc["value_mae"],
                "throughput_eps": perf["throughput_eps"],
                "p50_ms": perf["snap_latency_ms"][50],
                "p99_ms": perf["snap_latency_ms"][99],
            }
        )
    return rows


def pending_rows(engine):
    return [
        {
            "engine": engine,
            "query": name,
            "exact_match_rate": None,
            "f1": None,
            "value_mae": None,
            "throughput_eps": None,
            "p50_ms": None,
            "p99_ms": None,
            "note": _PENDING,
        }
        for name in queries.ALL
    ]


def add_engine_from_file(engine, query, path, throughput_eps=None):
    """Grade a live engine's dumped results file (checker.grade) into a table row.

    Throughput/latency for live engines come from observation (Flink UI / sink rate) and are
    passed in or left blank; accuracy comes from the graded dump."""
    acc = checker.grade(query, path)
    return {
        "engine": engine,
        "query": query,
        "exact_match_rate": acc["exact_match_rate"],
        "f1": acc["f1"],
        "value_mae": acc["value_mae"],
        "throughput_eps": throughput_eps,
        "p50_ms": None,
        "p99_ms": None,
        "orphan_records": acc["orphan_records"],
    }


def _merge(rows, new_row):
    for i, r in enumerate(rows):
        if r["engine"] == new_row["engine"] and r["query"] == new_row["query"]:
            rows[i] = new_row
            return rows
    rows.append(new_row)
    return rows


def _fmt(v, spec):
    return "—" if v is None else format(v, spec)


def to_markdown(rows):
    hdr = (
        "| engine | query | exact | F1 | val MAE | throughput (eps) | p50 (ms) | p99 (ms) |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    body = ""
    for r in rows:
        body += (
            f"| {r['engine']} | {r['query']} | "
            f"{_fmt(r['exact_match_rate'], '.4f')} | {_fmt(r['f1'], '.4f')} | "
            f"{_fmt(r['value_mae'], '.2f')} | {_fmt(r['throughput_eps'], ',.0f')} | "
            f"{_fmt(r['p50_ms'], '.3f')} | {_fmt(r['p99_ms'], '.3f')} |\n"
        )
    return hdr + body


def _write(rows):
    md = _render(rows)
    with open("results.md", "w") as f:
        f.write(md)
    with open("results.json", "w") as f:
        json.dump(rows, f, indent=2)
    return md


def _render(rows):
    return (
        "# Phase 1 results — {engine × query × metric}\n\n"
        "Accuracy is graded vs the DuckDB oracle (symmetric, per-snapshot); throughput +\n"
        "latency from benchmark.py. The reference maintainer is exact by construction (it\n"
        "self-tests the harness) and stands in for the bespoke engine until Phase 3. Flink\n"
        "and RisingWave rows fill in once their infra is up (baselines/*/README.md).\n\n"
        + to_markdown(rows)
        + "\nLatency here is in-process per-snapshot COMPUTE time; end-to-end Kafka latency\n"
        "for the live engines is marker-based (CONTRACT.md §7).\n"
    )


def main():
    ap = argparse.ArgumentParser(description="Collect the {engine x query x metric} table.")
    ap.add_argument(
        "--add",
        nargs=3,
        metavar=("ENGINE", "QUERY", "FILE"),
        help="grade a live-engine results dump and merge it into results.json/md",
    )
    ap.add_argument(
        "--throughput",
        type=float,
        default=None,
        help="observed throughput (eps) to attach to the --add row",
    )
    args = ap.parse_args()

    if args.add:
        engine, query, path = args.add
        if not os.path.exists("results.json"):
            raise SystemExit("run `python3 benchmark/results.py` first to create results.json")
        with open("results.json") as f:
            rows = json.load(f)
        rows = _merge(rows, add_engine_from_file(engine, query, path, args.throughput))
        print(_write(rows))
        print(f"merged {engine}/{query} from {path}")
        return 0

    rows = reference_rows() + pending_rows("flink") + pending_rows("risingwave")
    print(_write(rows))
    print("wrote results.md, results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
