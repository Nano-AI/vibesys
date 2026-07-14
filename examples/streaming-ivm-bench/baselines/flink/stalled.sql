-- Flink SQL baseline: Q4 stalled requests (anti-join) -- TASKS.md 1.9.
--
-- ============================ WHY THIS QUERY IS HARD IN FLINK SQL ============================
-- Q4 is a windowed ANTI-JOIN with a temporal predicate: a failed request is stalled iff no
-- LATER same-user success exists within the window. Flink's windowing TVFs aggregate; they
-- do not express "no later matching row in the same window". The natural encodings are:
--   (a) an interval anti-join (events e LEFT JOIN events s ON same user, s later, within W,
--       s success; keep e with no match) -- an unbounded/interval join that materializes a
--       RETRACT stream and needs state TTL tuning, or
--   (b) MATCH_RECOGNIZE / a DataStream ProcessFunction with a keyed timer per user.
-- Both are awkward and retraction-heavy. This is exactly the non-monotonic anti-join shape
-- the thesis targets: general streaming SQL either can't express it cleanly or pays in
-- state + accuracy at the leave-edge. The reference maintainer (maintainers.py) does it in
-- one pass; a bespoke engine is expected to as well.
--
-- Below is best-effort form (a): an interval anti-join, emitted through an UPSERT sink keyed
-- by request_id so a later success RETRACTS (tombstones) the stalled row. It is NOT snapshot
-- aligned by construction, so the checker will show measured accuracy loss vs the oracle --
-- reported, not hidden. Prefer the DataStream job for a faithful run.
-- ============================================================================================

CREATE TABLE results_stalled (
  snapshot_ts DOUBLE,
  `key`       STRING,
  `value`     STRING,                 -- membership-only; carried as null
  PRIMARY KEY (`key`) NOT ENFORCED
) WITH (
  'connector'                    = 'upsert-kafka',
  'topic'                        = 'results.stalled',
  'properties.bootstrap.servers' = 'kafka:9092',
  'key.format'                   = 'json',
  'value.format'                 = 'json'
);

-- Interval anti-join: failed e with no later same-user success within W seconds.
INSERT INTO results_stalled
SELECT
  CAST(e.ts AS DOUBLE) AS snapshot_ts,       -- best-effort; true snapshot alignment needs a timer
  e.request_id         AS `key`,
  CAST(NULL AS STRING) AS `value`
FROM events e
WHERE e.status <> 'success'
  AND NOT EXISTS (
    SELECT 1 FROM events s
    WHERE s.user_id = e.user_id
      AND s.status = 'success'
      AND s.event_time > e.event_time
      AND s.event_time <= e.event_time + INTERVAL '60' SECOND
  );
