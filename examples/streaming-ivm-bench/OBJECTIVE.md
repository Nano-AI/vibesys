# Objective — bespoke streaming engine for non-monotonic + windowed SQL

Maximize **sustained throughput (events/sec)** of a native engine that incrementally
maintains a set of **non-monotonic, time-windowed** queries over an LLM-serving telemetry
stream, **at correctness parity with the DuckDB oracle**. Accuracy is a hard gate (below);
among engines that clear it, higher `events_per_sec` wins.

The candidate is a **compiled, native engine** (e.g. Rust built with `cargo build --release`),
NOT a Python `main.py`. It reads the event stream and emits result records per the I/O
contract; it is graded and benchmarked as a subprocess over files — the framework never
imports it.

- **Headline metric**: `events_per_sec` from `benchmark/benchmark.py`'s `--output-json`
  output (the `Primary metric: events_per_sec = ...` line). This is the only number
  `perf_metric` should record; the unit is `events_per_sec` and must not change between rounds.

## Workload

A seeded (`SEED=42`) stream of ~6.2k request-completion events over 300s of event-time
(schema mirrors Langfuse / OpenTelemetry-GenAI logs: `ts, user_id, project_id, model,
input/output/reasoning_tokens, cost_usd, status, ...`). Four queries, each maintained over a
sliding window `W=60s` and reported on a snapshot grid `t_k = k·1s` (301 snapshots):

- **Q1 `metering`** — per-user `SUM(total_tokens)` over the window, flag users above
  `BUDGET_TOKENS` (SUM + HAVING; retracts when a burst ages out).
- **Q2 `active_users`** — per-project `COUNT(DISTINCT user_id)` over the window (distinct-state cost).
- **Q3 `top_cost`** — top-K costliest models by exact integer micro-dollars over the window (windowed Top-N).
- **Q4 `stalled`** — failed requests with no later same-user success in the window (anti-join / NOT EXISTS).

These are chosen because each is **non-monotonic** in a different way (threshold retraction,
distinct state, ranking churn, anti-join membership) — exactly where general streaming
engines lose accuracy or pay to preserve it, and where a bespoke engine aims to be both
correct and cheap. The workload is **engine-neutral**: defined by the data, not tuned to any
engine's window model.

## Correctness gate (accuracy is measured, symmetric, and required)

`accuracy_checker/checker.py` grades the engine's output file against the DuckDB oracle
(`reference/`) per snapshot, after settling the changelog (last-write-wins per
`(snapshot_ts, key)`, honoring retraction/tombstones). To pass the Judge, an engine must
reproduce the oracle with **exact-match snapshot rate = 1.0** (no off-grid/orphan records) on
the queries it claims to support. Getting a snapshot wrong is counted accuracy loss, not a
crash — but the gate for advancing a candidate is exactness on the implemented queries.

## Notes

- **I/O contract**: see `CONTRACT.md`. Input = one JSON event per record (file/stdin/Kafka,
  key = `user_id`, non-decreasing `ts`). Output = `{snapshot_ts, key, value}` records
  (JSONL/CSV or Kafka `results.<query>`), **settled last-wins**; retraction via an explicit
  delete / Kafka tombstone. `value` is null for pure-membership queries (Q4).
- **Window semantics**: a row is in-window at `t` iff `(t − W) < ts ≤ t`; `now()` binds to the
  snapshot event-time `t_k`, never wall-clock. Window-model mismatches surface as the engine's
  own accuracy loss (we do not align the grid to any engine).
- **Target**: native CPU (`--backend cpu`); no GPU/CUDA. Build once, run as a process.
- **Params** (`W`, `S`, `BUDGET`, `TOP_K`, topics) come from `reference/core/config.py` — the
  single source of truth shared by the generator, oracle, and every engine.
