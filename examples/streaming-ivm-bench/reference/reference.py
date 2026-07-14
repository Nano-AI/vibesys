"""Reference oracle entrypoint (vibe-database `--ref`).

The correctness ground truth for this target is the DuckDB batch-recompute **oracle**
(`core/oracle.py`): at each event-time snapshot t_k it recomputes the query over all events
with ts <= t_k, binding `now() = t_k`. Every engine under test — Flink, RisingWave, and the
synthesized bespoke engine — is scored on per-snapshot deviation from this oracle (accuracy
is a measured, symmetric metric, not a pass/fail gate; see DESIGN.md).

This is the single top-level `.py` in `reference/` (vibe-database requires exactly one); the
shared source-of-truth modules live in `core/` and are put on the import path below. Run
directly, it prints the oracle's changelog for a query — a human-readable view of "the one
correct answer" at each snapshot.

    python3 reference.py --query metering            # print the oracle changelog
"""

import argparse
import os
import sys

# The shared truth (config, queries, oracle, maintainers, generator, harness) lives in
# core/. Adding it to sys.path lets those modules keep their flat imports (`import config`,
# `from oracle import Oracle`) unchanged, and lets the accuracy_checker/ and benchmark/
# slots import the same core via `../reference/core`.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))

import queries  # noqa: E402
from harness import snapshots  # noqa: E402
from oracle import Oracle  # noqa: E402

# Re-export so callers can `from reference import Oracle`.
__all__ = ["Oracle", "snapshots", "queries"]


def main():
    ap = argparse.ArgumentParser(description="Print the DuckDB oracle changelog for a query.")
    ap.add_argument("--query", default="metering", choices=queries.ALL)
    ap.add_argument("--limit", type=int, default=10, help="print at most this many snapshots")
    args = ap.parse_args()
    o = Oracle(args.query)
    snaps = snapshots()
    shown = 0
    for now in snaps:
        row = o.snapshot(now)
        if row:
            print(f"t={now:8.2f}  {row}")
            shown += 1
            if shown >= args.limit:
                print(f"... ({len(snaps)} snapshots total; use --limit to see more)")
                break
    o.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
