"""Driver / harness self-test: generate (if needed) -> for every registered query, run the
oracle + the exact reference maintainer -> score per-snapshot accuracy.

This validates the benchmark harness itself: each reference maintainer is exact, so it must
score accuracy 1.0 against the oracle for its query. Once real engines exist (Flink,
RisingWave, the synthesized Rust engine), each is driven the same way and scored with the
same `accuracy.score` -- correctness is a measured, symmetric metric, not a pass/fail gate
(DESIGN.md §5-6.3). Together with checker.py (grading engine output files) this is the
engine-agnostic accuracy checker (TASKS.md 1.6).

Usage:  python3 run.py
"""

import os
import sys

# Shared truth (config, queries, generator, oracle helpers) lives in the reference core/.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "core")
)

import accuracy  # noqa: E402  (local to accuracy_checker/)
import config  # noqa: E402
import generate  # noqa: E402
import queries  # noqa: E402
from harness import load_events, non_monotonicity, oracle_results, snapshots  # noqa: E402


def main():
    if not os.path.exists(config.EVENTS_CSV):
        n = generate.generate()
        print(f"generated {n} events")
    events = load_events(config.EVENTS_CSV)
    snaps = snapshots()
    print(
        f"events={len(events)}  snapshots={len(snaps)}  "
        f"window={config.WINDOW_SECONDS:.0f}s  budget={config.BUDGET_TOKENS:,}\n"
    )

    all_exact = True
    suite_retractions = 0
    for name in queries.ALL:
        q = queries.get(name)
        truth = oracle_results(q, snaps)
        maint_results = list(q.maintainer().run(events, snaps))
        scores = accuracy.score(truth, maint_results)
        mem_leave, val_drop = non_monotonicity(truth)

        # Harness integrity = the reference maintainer reproduces the oracle exactly.
        # Retraction is a WORKLOAD property, characterized per query and required only
        # suite-wide (Q2's distinct count saturates under W=60s -- its role is the
        # approximate-distinct value-accuracy axis, not retraction).
        exact = scores["exact_match_rate"] == 1.0
        all_exact = all_exact and exact
        suite_retractions += mem_leave + val_drop

        print("=" * 66)
        print(f"{q.title}  [{name}]   maintainer exact: {'OK' if exact else 'BROKEN'}")
        print("-" * 66)
        print(
            f"  exact-match snapshot rate : {scores['exact_match_rate']:.4f}  "
            f"({scores['exact_matches']}/{scores['snapshots']})"
        )
        print(
            f"  precision / recall / F1   : "
            f"{scores['precision']:.4f} / {scores['recall']:.4f} / {scores['f1']:.4f}"
        )
        print(
            f"  value MAE / max error     : {scores['value_mae']:.2f} / {scores['value_max_err']}"
        )
        print(
            f"  non-monotonicity          : {mem_leave} membership retractions, "
            f"{val_drop} value drops"
        )
    ok = all_exact and suite_retractions > 0
    print("=" * 66)
    print(
        f"HARNESS SELF-TEST  (all maintainers exact: {all_exact}; "
        f"suite retractions: {suite_retractions}):  {'OK' if ok else 'BROKEN'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
