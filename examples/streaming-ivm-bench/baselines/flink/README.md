# Flink baseline (TASKS.md 1.9)

Apache Flink SQL realizations of the four benchmark queries, conforming to the I/O contract
(`../../CONTRACT.md`): read the shared `llm-events` Kafka topic (event-time = `ts`,
bounded-out-of-orderness 0), emit `(snapshot_ts, key, value)` to `results.<query>`.

## Files
| file | query | realization |
|------|-------|-------------|
| `metering.sql`     | Q1 metering        | HOP TVF + `HAVING` (append-only sink) |
| `active_users.sql` | Q2 active users    | HOP TVF + exact `COUNT(DISTINCT)` |
| `top_cost.sql`     | Q3 top-k cost      | HOP TVF + window Top-N (`ROW_NUMBER`) |
| `stalled.sql`      | Q4 stalled (anti)  | interval anti-join + upsert sink (**hard**; see file header) |

## Window-semantics accuracy caveat (measured, not hidden)
Flink `HOP` windows are half-open `[window_start, window_end)`; the oracle observable is
`(t_k - W, t_k]`. They differ only on events landing exactly on a boundary (`ts == t_k` or
`ts == t_k - W`) — measure-zero for continuous `ts`, but any occurrence is real accuracy
loss and the checker quantifies it (`../../DESIGN.md` §9). We do not bend the observable to
Flink. Q4 additionally is not snapshot-aligned in SQL — its accuracy loss is expected and is
the point of the query.

## Run
Prereq: drop the Kafka SQL connector jar into `./lib/` (mounted into the Flink containers):
`flink-sql-connector-kafka-<ver>.jar` matching Flink 1.20 (from Maven Central).

```bash
# from the repo root
docker compose up -d kafka flink-jobmanager flink-taskmanager

# load the stream (correctness run: single partition)
python3 load_driver.py --to kafka --speed 50 --partitions 1

# submit a query (metering shown; others: swap the -f target)
docker compose run --rm flink-sql-client            # runs /opt/sql/metering.sql
# for another query:
docker compose run --rm --entrypoint /opt/flink/bin/sql-client.sh flink-sql-client -f /opt/sql/top_cost.sql

# grade it
python3 dump_results.py --query metering --out flink_metering.jsonl --timeout 15
python3 checker.py --query metering --engine-output flink_metering.jsonl

# throughput (saturation): re-run load_driver with --asap --partitions 8 and watch the
# Flink UI (:8081) busy/backpressure + records-out rate.
```

Record the exact Flink version + connector jar version alongside results (reproducibility).
