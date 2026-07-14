-- RisingWave baseline for the streaming-ivm-bench I/O contract (TASKS.md 1.10).
-- Connect with psql: `psql -h localhost -p 4566 -d dev -U root -f metering.sql`
--
-- Shared source (create once), then one materialized view + upsert-kafka sink per query.
-- The MV maintains the windowed aggregate incrementally and RETRACTS via the changelog; the
-- upsert sink emits a Kafka tombstone when a key leaves the flagged set, which dump_results
-- collapses to "absent at that snapshot" (CONTRACT.md §3).
--
-- WINDOW-SEMANTICS CAVEAT: RisingWave HOP windows, like Flink, are half-open
-- [window_start, window_end); the oracle observable is (t_k - W, t_k]. Boundary-only
-- disagreements are measured by the checker, not hidden (DESIGN.md §9).

-- Input event stream (CONTRACT.md §1). event_time from ts (seconds since T0=0).
CREATE SOURCE IF NOT EXISTS events (
  ts               DOUBLE PRECISION,
  request_id       VARCHAR,
  user_id          VARCHAR,
  project_id       VARCHAR,
  session_id       VARCHAR,
  model            VARCHAR,
  input_tokens     INTEGER,
  output_tokens    INTEGER,
  reasoning_tokens INTEGER,
  cost_usd         DOUBLE PRECISION,
  latency_ms       INTEGER,
  status           VARCHAR,
  finish_reason    VARCHAR,
  ingest_ts        DOUBLE PRECISION,
  event_time       TIMESTAMPTZ AS to_timestamp(ts),
  WATERMARK FOR event_time AS event_time - INTERVAL '0' SECOND   -- bounded-out-of-order = 0
) WITH (
  connector          = 'kafka',
  topic              = 'llm-events',
  properties.bootstrap.server = 'kafka:9092',
  scan.startup.mode  = 'earliest'
) FORMAT PLAIN ENCODE JSON;

-- Q1 metering: SUM(total_tokens) per user over the window, flagged > budget.
CREATE MATERIALIZED VIEW metering AS
SELECT
  extract(epoch FROM window_end)::DOUBLE PRECISION AS snapshot_ts,
  user_id                                          AS key,
  SUM(input_tokens + output_tokens + reasoning_tokens) AS value
FROM HOP(events, event_time, INTERVAL '1' SECOND, INTERVAL '60' SECOND)
GROUP BY window_end, user_id
HAVING SUM(input_tokens + output_tokens + reasoning_tokens) > 60000;

-- Settled output: upsert keyed by (snapshot_ts, key); retractions become tombstones.
CREATE SINK metering_sink FROM metering
WITH (
  connector          = 'kafka',
  topic              = 'results.metering',
  properties.bootstrap.server = 'kafka:9092',
  primary_key        = 'snapshot_ts,key'
) FORMAT UPSERT ENCODE JSON;
