# streaming-ivm-bench (MVP)

A scriptable benchmark for the thesis: **a bespoke, workload-specialized engine
(synthesized by vibe-database) maintains non-monotonic + time-windowed SQL better than
general streaming engines (Flink, RisingWave, Materialize)** — measured by throughput,
latency, and **per-snapshot accuracy**.

Phase 1 is complete (agent-free): a deterministic engine-neutral generator, a DuckDB oracle
defining the one correct answer for **four** non-monotonic + windowed queries, exact
reference maintainers with retraction, an engine-agnostic accuracy checker + benchmark
harness, a Kafka substrate + load driver, and Flink + RisingWave baseline adapters. The
synthesized Rust engine (Phase 2-3) plugs into the same I/O contract and is graded by the
same code.

## Run (no infra needed)

```bash
pip install duckdb
python3 run.py                 # generate events.csv (first run), then self-test all queries
python3 checker.py --selftest  # round-trip the engine-agnostic accuracy checker
python3 benchmark.py --query metering   # throughput + latency; prints `Primary metric:`
python3 results.py             # {engine × query × metric} -> results.md, results.json
```

`run.py` expects `HARNESS SELF-TEST ... OK`: every exact reference maintainer scores 1.0
against the oracle, and the workload shows retractions (suite total > 0).

## Run the live baselines (needs Docker)

```bash
docker compose up -d kafka                       # fair ingestion substrate
python3 load_driver.py --to kafka --speed 50 --partitions 1     # correctness run
# then bring up an engine and grade it (see baselines/flink|risingwave/README.md):
python3 dump_results.py --query metering --out eng.jsonl
python3 checker.py --query metering --engine-output eng.jsonl
python3 results.py --add flink metering eng.jsonl
```

## Queries (all engine-neutral, all non-monotonic + windowed)

| id | query | operator | non-monotonic axis |
|----|-------|----------|--------------------|
| Q1 `metering`     | per-user windowed token sum > budget | SUM + HAVING | flagged set retracts as bursts age out |
| Q2 `active_users` | per-project COUNT(DISTINCT user) | COUNT DISTINCT | distinct STATE cost / approx-distinct value error |
| Q3 `top_cost`     | top-k costliest models | window Top-N | ranking reorders/retracts on expiry |
| Q4 `stalled`      | failed requests with no later same-user success | anti-join / NOT EXISTS | retracted by a later success or expiry |

## Workload (engine-neutral)

Token metering / quota:

> per user, `SUM(total_tokens)` over the last `WINDOW_SECONDS` (sliding, event-time);
> flag users whose windowed sum exceeds `BUDGET_TOKENS`.

Non-monotonic: a user **enters** the flagged set on a burst and **leaves** it (a
*retraction*) when the burst ages out of the window. The leave-edge is the property
under test — where general engines can lose accuracy or pay to preserve it. The
workload is defined by the data, **tuned to no engine**; the oracle is the single
correct answer and every engine is scored on deviation from it.

## Correctness = a measured, symmetric metric (not a gate)

Every engine — Flink, RisingWave, and the bespoke engine alike — is scored per snapshot
against the oracle via `accuracy.py`:
- **exact-match snapshot rate** (headline)
- **set precision / recall / F1** on flagged membership (partial credit)
- **value MAE / max error** on the aggregate (right-set-wrong-number)

An engine that is fast but approximate reports high throughput *and* lower accuracy —
the tradeoff is shown, not hidden. See `DESIGN.md` §5-6.3.

## Files

| file | role | maps to vibe-database target slot |
|------|------|-------------------------------|
| `config.py` | shared window/budget/snapshot semantics, prices, infra params | — |
| `generate.py` | deterministic, seeded, engine-neutral event generator → `events.csv` | workload/data |
| `queries/*.sql` | the four queries; each defines its one correct answer | the objective |
| `queries.py` | query registry tying SQL + params + reference maintainer together | — |
| `oracle.py` | DuckDB batch recompute per snapshot = ground truth (any query) | `reference/` |
| `accuracy.py` | engine-agnostic per-snapshot accuracy scorer (null-safe) | `accuracy_checker/` |
| `checker.py` | grade any engine's output file vs the oracle (settled last-wins) | `accuracy_checker/` driver |
| `maintainer.py` / `maintainers.py` | exact reference maintainers Q1 / Q2-Q4 (self-test + Rust seed) | Python correctness reference |
| `benchmark.py` | throughput + latency harness; emits `Primary metric:` | `benchmark/` |
| `run.py` | harness self-test: oracle + reference maintainers + accuracy, all queries | `accuracy_checker/` driver |
| `load_driver.py` | replay events into Kafka/file at a target rate, stamping ingest time | workload feeder |
| `dump_results.py` | consume a live engine's results topic → checker input file | grading glue |
| `results.py` | collect `{engine × query × metric}` → `results.md` / `results.json` | results |
| `docker-compose.yml` | Kafka + RisingWave + Flink services | infra |
| `baselines/flink/`, `baselines/risingwave/` | live-engine SQL + adapters + READMEs | baselines |

## What this is / isn't

- **Is:** the complete agent-free Phase-1 benchmark — engine-neutral data, an independent
  SQL oracle for four non-monotonic + windowed queries, exact reference maintainers, a
  reusable accuracy checker + throughput/latency harness, a Kafka substrate + load driver,
  and Flink + RisingWave baseline adapters conforming to one I/O contract.
- **Isn't yet:** live baseline *numbers* (the SQL/adapters/compose are written and validated
  but not run here — needs `docker compose up`); the vibe-database-synthesized Rust engine and
  the CPU/native backend (Phase 2); the full sweep + head-to-head results (Phase 3).

## Extending

Add a query = add `queries/<name>.sql` + a `queries.py` registry entry + a reference
maintainer. The generator schema already carries `model`, `status`, `cost_usd`,
`session_id`, so COUNT(DISTINCT) / top-k / anti-join need no new data.
