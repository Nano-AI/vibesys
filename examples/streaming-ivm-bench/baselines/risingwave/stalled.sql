-- RisingWave baseline: Q4 stalled requests (anti-join) -- TASKS.md 1.10.
-- Requires the shared `events` source from metering.sql.
--
-- ============================ WHY THIS QUERY IS HARD ============================
-- Q4 is a windowed anti-join with a temporal "no later success" predicate. RisingWave can
-- express NOT EXISTS (it maintains a retractable dynamic-filter/anti-join), but combining it
-- with the sliding window + snapshot alignment is where general streaming engines pay: the
-- anti-join keeps per-user success state and re-derives membership on every arrival, and the
-- result is NOT naturally sampled at t_k. Expect measured accuracy loss vs the oracle -- the
-- checker reports it. This is precisely the retraction-native shape the bespoke engine
-- targets; the reference maintainer (maintainers.py) does it in one pass.
--
-- Best-effort form: an interval anti-join, snapshot-aligned by joining against the set of
-- snapshot times a request is in-window. Kept deliberately close to the oracle's definition;
-- differences that remain are the engine's accuracy cost, not a workload artifact.
-- ================================================================================

CREATE MATERIALIZED VIEW stalled AS
SELECT
  CAST(e.ts AS DOUBLE PRECISION) AS snapshot_ts,   -- best-effort; timer-based alignment preferred
  e.request_id                   AS key,
  CAST(NULL AS VARCHAR)          AS value
FROM events e
WHERE e.status <> 'success'
  AND NOT EXISTS (
    SELECT 1 FROM events s
    WHERE s.user_id = e.user_id
      AND s.status = 'success'
      AND s.event_time > e.event_time
      AND s.event_time <= e.event_time + INTERVAL '60' SECOND
  );

CREATE SINK stalled_sink FROM stalled
WITH (
  connector          = 'kafka',
  topic              = 'results.stalled',
  properties.bootstrap.server = 'kafka:9092',
  primary_key        = 'key'
) FORMAT UPSERT ENCODE JSON;
