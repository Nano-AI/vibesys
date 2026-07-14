# reference/ — the correctness ground truth (VibeServe `--ref`)

The reference for this target is the **DuckDB batch-recompute oracle**: at each event-time
snapshot `t_k` it recomputes the query over all events with `ts ≤ t_k` (binding `now() = t_k`)
and yields `{key: value}`. That sequence is the one correct answer every engine — Flink,
RisingWave, and the synthesized bespoke engine — is scored against by
`accuracy_checker/checker.py`. Accuracy is a **measured, symmetric metric**, not tuned to any
engine's window model (see `DESIGN.md`, `CONTRACT.md`).

## Layout

- `reference.py` — the single top-level entrypoint (VibeServe requires exactly one `.py` in a
  `reference/` dir). Re-exports `Oracle`; run directly to print the oracle changelog for a query.
- `core/` — shared source-of-truth modules, placed on the import path by `reference.py` and by
  the `accuracy_checker/` and `benchmark/` slots (via `../reference/core`). Keeping them here
  means all three slots agree on window/threshold/snapshot semantics:
  - `config.py` — single source of truth for window `W`, budget, snapshot interval, prices,
    topics, and the anchored `EVENTS_CSV` path.
  - `queries.py` + `queries/*.sql` — the four non-monotonic + windowed query definitions.
  - `oracle.py` — the DuckDB oracle (ground truth).
  - `maintainer.py`, `maintainers.py` — exact Python reference maintainers (a **correctness**
    reference / seed for the bespoke engine — NOT a throughput contender).
  - `generate.py` — the seeded (`SEED=42`) event generator → `core/events.csv`.
  - `harness.py` — shared helpers: the snapshot grid, event loading, the oracle changelog,
    and a non-monotonicity characterizer.
  - `events.csv` — the generated event stream (gitignored; regenerate with
    `python3 reference/core/generate.py`).

## Not the reference (deliberately)

The Python maintainers are **not** the VibeServe reference in the correctness sense — the
oracle is. They exist to (a) self-test the harness (each is exact, so it scores 1.0 vs the
oracle) and (b) seed the eventual Rust engine. See `DESIGN.md` §2 and `TASKS.md`.
