"""Shared harness helpers: the snapshot grid, event loading, the oracle changelog, and a
non-monotonicity characterizer.

Extracted from the former flat `run.py` so the accuracy checker (accuracy_checker/) and the
benchmark (benchmark/) share ONE definition of "the snapshots" and "the oracle's answer at
each snapshot" instead of each re-deriving it. Lives in reference/core/ because it is part
of how truth is defined (it drives the DuckDB oracle).
"""

import csv

import config
from oracle import Oracle


def load_events(path):
    """Read the seeded event CSV into a list of dict rows with numeric fields coerced."""
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["ts"] = float(r["ts"])
        for k in ("input_tokens", "output_tokens", "reasoning_tokens", "latency_ms"):
            r[k] = int(r[k])
    return rows


def snapshots():
    """The comparison grid t_k = k * SNAPSHOT_INTERVAL over [0, SIM_DURATION]."""
    t, out = 0.0, []
    while t <= config.SIM_DURATION + 1e-9:
        out.append(round(t, 4))
        t += config.SNAPSHOT_INTERVAL
    return out


def oracle_results(query, snaps, events_csv=None):
    """The oracle's changelog: [(t_k, {key: value}), ...] — the one correct answer per snapshot."""
    o = Oracle(query, events_csv=events_csv)
    res = [(now, o.snapshot(now)) for now in snaps]
    o.close()
    return res


def non_monotonicity(results):
    """Count retraction-style changes: keys that LEAVE the flagged set (membership
    retractions) and existing keys whose value DECREASES (value retractions). A workload
    that exercises neither is not testing the property under test."""
    mem_leave = val_drop = 0
    prev = {}
    for _, res in results:
        cur = res
        mem_leave += len(set(prev) - set(cur))
        for k in set(prev) & set(cur):
            if prev[k] is not None and cur[k] is not None and cur[k] < prev[k]:
                val_drop += 1
        prev = cur
    return mem_leave, val_drop
