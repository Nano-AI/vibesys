-- RisingWave baseline: Q3 top-k costliest models -- TASKS.md 1.10.
-- Requires the shared `events` source from metering.sql. Windowed cost aggregate (exact
-- integer micro-dollars, mirroring config.PRICE_MILLI) then a per-snapshot Top-N via
-- ROW_NUMBER. The ranking retracts as costly requests age out -- RisingWave maintains the
-- Top-N incrementally with retraction. CASE MUST mirror config.PRICE_MILLI.

CREATE MATERIALIZED VIEW top_cost AS
SELECT snapshot_ts, key, value
FROM (
  SELECT
    extract(epoch FROM window_end)::DOUBLE PRECISION AS snapshot_ts,
    model AS key,
    SUM((input_tokens + output_tokens + reasoning_tokens) *
        CASE model
          WHEN 'gpt-4o'          THEN 5000
          WHEN 'claude-sonnet-5' THEN 3000
          WHEN 'claude-opus-4-8' THEN 15000
          WHEN 'gpt-4o-mini'     THEN 600
          WHEN 'llama-3-70b'     THEN 900
        END) AS value,
    ROW_NUMBER() OVER (
      PARTITION BY window_end
      ORDER BY SUM((input_tokens + output_tokens + reasoning_tokens) *
          CASE model
            WHEN 'gpt-4o'          THEN 5000
            WHEN 'claude-sonnet-5' THEN 3000
            WHEN 'claude-opus-4-8' THEN 15000
            WHEN 'gpt-4o-mini'     THEN 600
            WHEN 'llama-3-70b'     THEN 900
          END) DESC, model ASC
    ) AS rn
  FROM HOP(events, event_time, INTERVAL '1' SECOND, INTERVAL '60' SECOND)
  GROUP BY window_end, model
)
WHERE rn <= 3;

CREATE SINK top_cost_sink FROM top_cost
WITH (
  connector          = 'kafka',
  topic              = 'results.top_cost',
  properties.bootstrap.server = 'kafka:9092',
  primary_key        = 'snapshot_ts,key'
) FORMAT UPSERT ENCODE JSON;
