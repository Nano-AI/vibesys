"""Engine-agnostic accuracy checker (TASKS.md 1.6).

Grades ANY engine's output against the DuckDB oracle, at the process level, using only the
I/O contract (CONTRACT.md §3): the engine emits result records `{snapshot_ts, key, value}`
(JSONL or CSV; flagged rows only). Flink, RisingWave, the Rust reference and the
synthesized engine are all graded by this one code path -- no per-engine forks.

Pipeline:
  1. read the engine's result records (file; a Kafka `RESULTS_TOPIC.<query>` dump is just a
     file of the same records),
  2. materialize SETTLED snapshots -- last record wins per (snapshot_ts, key) after the
     stream drains (contract §3), giving `snapshot_ts -> {key: value}`,
  3. align to the oracle's snapshot grid and score with accuracy.score (symmetric metric).

Also provides `write_results` -- the contract's output PRODUCER -- used by the oracle,
results.py, and this module's round-trip self-test.

Usage:
  python3 checker.py --query metering --engine-output out.jsonl
  python3 checker.py --selftest                      # round-trip the reference maintainer
"""

import argparse
import csv
import json
import os
import sys

# Shared truth (config, queries, oracle helpers) lives in the reference slot's core/.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "core")
)

import accuracy  # noqa: E402  (local to accuracy_checker/)
import config  # noqa: E402
import queries  # noqa: E402
from harness import load_events, oracle_results, snapshots  # noqa: E402

_TOL = 1e-6  # snapshot_ts must land on the grid within this


def write_results(path, results):
    """Write contract result records (JSONL) from [(snapshot_ts, {key: value}), ...].

    Only flagged rows are emitted (contract §3); an empty snapshot produces no records.
    This is the reference PRODUCER of the output format every engine must match.
    """
    with open(path, "w") as f:
        for snapshot_ts, row in results:
            for key, value in row.items():
                f.write(json.dumps({"snapshot_ts": snapshot_ts, "key": key, "value": value}) + "\n")


def read_records(path):
    """Yield {snapshot_ts: float, key: str, value: number|None} from JSONL or CSV."""
    is_json = path.endswith(".jsonl") or path.endswith(".json")
    with open(path) as f:
        if is_json:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
        else:
            for row in csv.DictReader(f):
                v = row.get("value", "")
                yield {
                    "snapshot_ts": float(row["snapshot_ts"]),
                    "key": str(row["key"]),
                    "value": None if v in ("", "null", "None") else float(v),
                }


def materialize_settled(records, snaps):
    """Collapse an engine's changelog to settled per-snapshot results (contract §3).

    Returns (results, orphans): `results` is [(t_k, {key: value}), ...] aligned to `snaps`
    (empty dict where the engine flagged nothing); `orphans` counts records whose
    snapshot_ts did not land on the grid (a conformance red flag, surfaced not hidden).
    """
    grid = {round(t, 4): t for t in snaps}
    settled = {t: {} for t in snaps}  # t_k -> {key: value} (last op wins)
    orphans = 0
    for rec in records:
        gk = round(float(rec["snapshot_ts"]), 4)
        t = grid.get(gk)
        if t is None:  # not on the snapshot grid
            orphans += 1
            continue
        key = str(rec["key"])
        if rec.get("_deleted"):  # retraction: last op removes the key
            settled[t].pop(key, None)
        else:
            settled[t][key] = rec.get("value")
    results = [(t, settled[t]) for t in snaps]
    return results, orphans


def grade(query, engine_output_path, events_csv=None):
    """Score an engine output file for `query` against the oracle. Returns a report dict."""
    q = queries.get(query) if isinstance(query, str) else query
    snaps = snapshots()
    truth = oracle_results(q, snaps, events_csv=events_csv)
    engine, orphans = materialize_settled(read_records(engine_output_path), snaps)
    scores = accuracy.score(truth, engine)
    scores["orphan_records"] = orphans
    scores["query"] = q.name
    return scores


def _print_report(scores):
    print(f"query                     : {scores['query']}")
    print(
        f"exact-match snapshot rate : {scores['exact_match_rate']:.4f}  "
        f"({scores['exact_matches']}/{scores['snapshots']})"
    )
    print(
        f"precision / recall / F1   : "
        f"{scores['precision']:.4f} / {scores['recall']:.4f} / {scores['f1']:.4f}"
    )
    print(f"value MAE / max error     : {scores['value_mae']:.2f} / {scores['value_max_err']}")
    print(f"off-grid (orphan) records : {scores['orphan_records']}")


def _selftest():
    """Round-trip: emit each reference maintainer to a file, grade it back -> must be 1.0.
    Proves the producer (write_results), reader, settler and scorer agree end-to-end."""
    events = load_events(config.EVENTS_CSV)
    snaps = snapshots()
    tmp = "_selftest_results.jsonl"
    ok = True
    for name in queries.ALL:
        q = queries.get(name)
        results = list(q.maintainer().run(events, snaps))
        # also add stale duplicates to prove last-wins settling collapses the changelog
        write_results(tmp, results)
        scores = grade(q, tmp)
        exact = scores["exact_match_rate"] == 1.0 and scores["orphan_records"] == 0
        ok = ok and exact
        print(
            f"[{'OK' if exact else 'FAIL'}] {name}: "
            f"exact={scores['exact_match_rate']:.4f} orphans={scores['orphan_records']}"
        )
    if os.path.exists(tmp):
        os.remove(tmp)
    print("checker round-trip:", "OK" if ok else "BROKEN")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Grade an engine output file vs the oracle.")
    ap.add_argument("--query", default="metering", choices=queries.ALL)
    ap.add_argument("--engine-output", help="JSONL/CSV of {snapshot_ts,key,value} records")
    ap.add_argument("--events", default=None, help="events CSV (default config.EVENTS_CSV)")
    ap.add_argument("--selftest", action="store_true", help="round-trip the reference maintainer")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.engine_output:
        ap.error("--engine-output is required unless --selftest")
    _print_report(grade(args.query, args.engine_output, events_csv=args.events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
