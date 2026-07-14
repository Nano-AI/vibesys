-- RisingWave baseline: Q2 active-user cardinality (COUNT DISTINCT) -- TASKS.md 1.10.
-- Requires the shared `events` source from metering.sql.
--
-- RisingWave maintains COUNT(DISTINCT) EXACTLY, but must retain per-distinct-value state for
-- every in-window (project, user) pair -- that STATE COST (memory + update work) is the Q2
-- axis. Switching to `approx_count_distinct` trades that cost for value error; run both and
-- let the checker show the accuracy the speedup costs.

CREATE MATERIALIZED VIEW active_users AS
SELECT
  extract(epoch FROM window_end)::DOUBLE PRECISION AS snapshot_ts,
  project_id               AS key,
  COUNT(DISTINCT user_id)  AS value
FROM HOP(events, event_time, INTERVAL '1' SECOND, INTERVAL '60' SECOND)
GROUP BY window_end, project_id;

CREATE SINK active_users_sink FROM active_users
WITH (
  connector          = 'kafka',
  topic              = 'results.active_users',
  properties.bootstrap.server = 'kafka:9092',
  primary_key        = 'snapshot_ts,key'
) FORMAT UPSERT ENCODE JSON;
