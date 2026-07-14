# RisingWave baseline (TASKS.md 1.10)

RisingWave SQL realizations of the four queries, conforming to the I/O contract
(`../../CONTRACT.md`): a Kafka `SOURCE` on `llm-events` (event-time = `ts`), one
`MATERIALIZED VIEW` per query, and an upsert-kafka `SINK` to `results.<query>`. Retractions
are emitted as Kafka tombstones, which `dump_results.py` collapses to "absent at snapshot".

## Files
| file | query | realization |
|------|-------|-------------|
| `metering.sql`     | Q1 metering     | shared `SOURCE` + HOP MV + `HAVING` + upsert sink |
| `active_users.sql` | Q2 active users | HOP MV + exact `COUNT(DISTINCT)` (state-cost axis) |
| `top_cost.sql`     | Q3 top-k cost   | HOP MV + `ROW_NUMBER` Top-N (incremental retraction) |
| `stalled.sql`      | Q4 stalled      | interval anti-join MV (**hard**; see file header) |

`metering.sql` creates the shared `events` source; run it first, then the others reuse it.

## Window / anti-join caveats (measured, not hidden)
HOP windows are half-open `[start, end)` vs the oracle's `(t_k - W, t_k]` — boundary-only
differences, quantified by the checker. Q4's anti-join is not snapshot-aligned in SQL; its
accuracy loss is expected and is the point of the query (`../../DESIGN.md` §9).

## Run
```bash
# from the repo root
docker compose up -d kafka risingwave

# define source + views + sinks
psql -h localhost -p 4566 -d dev -U root -f baselines/risingwave/metering.sql
psql -h localhost -p 4566 -d dev -U root -f baselines/risingwave/top_cost.sql   # etc.

# load the stream (correctness run)
python3 load_driver.py --to kafka --speed 50 --partitions 1

# grade
python3 dump_results.py --query metering --out rw_metering.jsonl --timeout 15
python3 checker.py --query metering --engine-output rw_metering.jsonl

# throughput: SELECT * FROM rw_catalog... or watch sink rate; drive with --asap --partitions 8.
```

Record the RisingWave image version alongside results (reproducibility).
