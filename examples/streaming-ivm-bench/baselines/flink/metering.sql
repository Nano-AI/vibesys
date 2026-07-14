-- Flink SQL baseline for the streaming-ivm-bench I/O contract (TASKS.md 1.9).
--
-- Realizes the observable (CONTRACT.md §2) as a HOP windowing TVF: sampling continuous
-- sliding truth at interval S == HOP(size=W, slide=S) evaluated at window_end = t_k.
-- Windowed TVF aggregation is APPEND-ONLY (one row per (window_end, key) at watermark), so
-- the results topic is already settled -- no retraction churn to collapse.
--
-- ============================ ACCURACY CAVEAT (measured, not hidden) ============================
-- Flink's HOP window is half-open [window_start, window_end): it INCLUDES window_start and
-- EXCLUDES window_end. The oracle's observable is (t_k - W, t_k]: EXCLUDES the lower edge,
-- INCLUDES t_k. So at each snapshot Flink and the oracle disagree only on events landing
-- exactly on a boundary (ts == t_k or ts == t_k - W). With continuous double `ts` these are
-- measure-zero, but any occurrence is real per-snapshot accuracy loss and the checker will
-- quantify it (DESIGN.md §9). We do NOT bend the observable to Flink's window model.
-- ==============================================================================================

SET 'execution.runtime-mode' = 'streaming';
SET 'pipeline.name' = 'sib-metering';

-- Input: the shared event stream (CONTRACT.md §1). ts is event-time in seconds since T0=0.
CREATE TABLE events (
  ts               DOUBLE,
  request_id       STRING,
  user_id          STRING,
  project_id       STRING,
  session_id       STRING,
  model            STRING,
  input_tokens     INT,
  output_tokens    INT,
  reasoning_tokens INT,
  cost_usd         DOUBLE,
  latency_ms       INT,
  status           STRING,
  finish_reason    STRING,
  ingest_ts        DOUBLE,
  event_time AS TO_TIMESTAMP_LTZ(CAST(ts * 1000 AS BIGINT), 3),
  WATERMARK FOR event_time AS event_time - INTERVAL '0' SECOND   -- bounded-out-of-order = 0
) WITH (
  'connector'                     = 'kafka',
  'topic'                         = 'llm-events',
  'properties.bootstrap.servers'  = 'kafka:9092',
  'properties.group.id'           = 'sib-flink-metering',
  'scan.startup.mode'             = 'earliest-offset',
  'format'                        = 'json',
  'json.ignore-parse-errors'      = 'false'
);

-- Output: contract result records (snapshot_ts, key, value), flagged rows only (§3).
CREATE TABLE results_metering (
  snapshot_ts DOUBLE,
  `key`       STRING,
  `value`     BIGINT
) WITH (
  'connector'                     = 'kafka',
  'topic'                         = 'results.metering',
  'properties.bootstrap.servers'  = 'kafka:9092',
  'format'                        = 'json'
);

-- Q1 metering: SUM(total_tokens) per user over the window, flag > budget.
INSERT INTO results_metering
SELECT
  UNIX_TIMESTAMP(DATE_FORMAT(window_end, 'yyyy-MM-dd HH:mm:ss')) * 1.0 AS snapshot_ts,
  user_id AS `key`,
  SUM(input_tokens + output_tokens + reasoning_tokens)          AS `value`
FROM TABLE(
  HOP(TABLE events, DESCRIPTOR(event_time), INTERVAL '1' SECOND, INTERVAL '60' SECOND)
)
GROUP BY window_start, window_end, user_id
HAVING SUM(input_tokens + output_tokens + reasoning_tokens) > 60000;
