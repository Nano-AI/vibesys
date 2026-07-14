"""Per-snapshot accuracy scoring (engine-agnostic).

Grades any engine's per-snapshot results against the oracle. This is the seed of the
Phase-1 accuracy checker (TASKS.md 1.6): the SAME scoring is reused for Flink,
RisingWave, the Rust reference, and the synthesized engine. Correctness is a measured,
symmetric metric -- every engine, bespoke included, is scored identically (DESIGN.md §6.3).

Inputs are two aligned sequences of (now, result_dict), where result_dict maps
key -> aggregate value for the flagged rows at that snapshot.
"""


def _snapshot_scores(truth, got):
    keys_t, keys_g = set(truth), set(got)
    tp = len(keys_t & keys_g)
    if not keys_t and not keys_g:
        precision = recall = 1.0  # both empty => perfect
    else:
        precision = 1.0 if not keys_g else tp / len(keys_g)
        recall = 1.0 if not keys_t else tp / len(keys_t)
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    exact = truth == got
    # value error over the union of keys; None-valued queries (pure membership, e.g. the
    # anti-join Q4) contribute no value error and are scored on set membership alone.
    errs = []
    for k in keys_t | keys_g:
        vt, vg = truth.get(k), got.get(k)
        if vt is None and vg is None:
            continue
        errs.append(abs((vt or 0) - (vg or 0)))  # missing side => 0
    mae = sum(errs) / len(errs) if errs else 0.0
    maxerr = max(errs) if errs else 0
    return exact, precision, recall, f1, mae, maxerr


def score(oracle_results, engine_results):
    """Return the accuracy suite for one engine vs the oracle.

    Both args are aligned lists of (now, result_dict) at identical snapshots.
    """
    assert len(oracle_results) == len(engine_results), "snapshot count mismatch"
    n = len(oracle_results)
    exact = 0
    sp = sr = sf1 = smae = 0.0
    gmax = 0
    for (t_o, r_o), (t_e, r_e) in zip(oracle_results, engine_results):
        assert t_o == t_e, f"snapshot time mismatch {t_o} != {t_e}"
        e, p, r, f1, mae, mx = _snapshot_scores(r_o, r_e)
        exact += 1 if e else 0
        sp += p
        sr += r
        sf1 += f1
        smae += mae
        gmax = max(gmax, mx)
    return {
        "snapshots": n,
        "exact_matches": exact,
        "exact_match_rate": exact / n,  # headline accuracy
        "precision": sp / n,  # macro-averaged over snapshots
        "recall": sr / n,
        "f1": sf1 / n,
        "value_mae": smae / n,  # mean abs error on the aggregate
        "value_max_err": gmax,
    }
