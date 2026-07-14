-- Flink SQL baseline: Q2 active-user cardinality (COUNT DISTINCT) -- TASKS.md 1.9.
-- Same events source as metering.sql (create it once per session). HOP TVF aggregation;
-- Flink's COUNT(DISTINCT ...) in a windowed aggregate is EXACT, so accuracy loss here is
-- only the half-open window boundary caveat (see metering.sql). Contrast RisingWave, whose
-- distinct in a materialized view can be approximate -- that is the Q2 value-accuracy axis.

CREATE TABLE results_active_users (
  snapshot_ts DOUBLE,
  `key`       STRING,
  `value`     BIGINT
) WITH (
  'connector'                    = 'kafka',
  'topic'                        = 'results.active_users',
  'properties.bootstrap.servers' = 'kafka:9092',
  'format'                       = 'json'
);

INSERT INTO results_active_users
SELECT
  UNIX_TIMESTAMP(DATE_FORMAT(window_end, 'yyyy-MM-dd HH:mm:ss')) * 1.0 AS snapshot_ts,
  project_id                AS `key`,
  COUNT(DISTINCT user_id)   AS `value`
FROM TABLE(
  HOP(TABLE events, DESCRIPTOR(event_time), INTERVAL '1' SECOND, INTERVAL '60' SECOND)
)
GROUP BY window_start, window_end, project_id;
