-- Flink SQL baseline: Q3 top-k costliest models -- TASKS.md 1.9.
-- Windowed aggregate + window Top-N (ROW_NUMBER over the window partition). Cost is in
-- exact integer micro-dollars (PRICE_MILLI * total_tokens), matching the oracle, so the
-- ranking is exact modulo the half-open window boundary caveat (see metering.sql).
-- The CASE MUST mirror config.PRICE_MILLI.

CREATE TABLE results_top_cost (
  snapshot_ts DOUBLE,
  `key`       STRING,
  `value`     BIGINT
) WITH (
  'connector'                    = 'kafka',
  'topic'                        = 'results.top_cost',
  'properties.bootstrap.servers' = 'kafka:9092',
  'format'                       = 'json'
);

INSERT INTO results_top_cost
SELECT snapshot_ts, `key`, `value`
FROM (
  SELECT
    UNIX_TIMESTAMP(DATE_FORMAT(window_end, 'yyyy-MM-dd HH:mm:ss')) * 1.0 AS snapshot_ts,
    model AS `key`,
    SUM((input_tokens + output_tokens + reasoning_tokens) *
        CASE model
          WHEN 'gpt-4o'          THEN 5000
          WHEN 'claude-sonnet-5' THEN 3000
          WHEN 'claude-opus-4-8' THEN 15000
          WHEN 'gpt-4o-mini'     THEN 600
          WHEN 'llama-3-70b'     THEN 900
        END) AS `value`,
    ROW_NUMBER() OVER (
      PARTITION BY window_start, window_end
      ORDER BY SUM((input_tokens + output_tokens + reasoning_tokens) *
          CASE model
            WHEN 'gpt-4o'          THEN 5000
            WHEN 'claude-sonnet-5' THEN 3000
            WHEN 'claude-opus-4-8' THEN 15000
            WHEN 'gpt-4o-mini'     THEN 600
            WHEN 'llama-3-70b'     THEN 900
          END) DESC, model ASC
    ) AS rn
  FROM TABLE(
    HOP(TABLE events, DESCRIPTOR(event_time), INTERVAL '1' SECOND, INTERVAL '60' SECOND)
  )
  GROUP BY window_start, window_end, model
)
WHERE rn <= 3;
