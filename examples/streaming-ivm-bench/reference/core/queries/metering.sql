-- Token metering / quota  --  the MVP non-monotonic + windowed workload.
--
-- "Which users have spent more than :budget total tokens in the last :window
--  seconds?"  Sliding event-time window; the flagged set retracts as bursts age
--  out. Parameters are bound per snapshot by oracle.py ($now = snapshot event-time,
--  NOT wall-clock -- binding wall-clock here is the classic streaming-correctness bug).
--
-- This statement defines the ONE correct answer for the workload. Every engine under
-- test (Flink, RisingWave, the bespoke engine) is graded against the result of running
-- it per snapshot; whatever an engine cannot reproduce exactly is that engine's
-- per-snapshot accuracy loss (DESIGN.md §5-6.3). The workload is engine-neutral.
SELECT
    user_id,
    SUM(input_tokens + output_tokens + reasoning_tokens) AS windowed_tokens
FROM events
WHERE ts > ($now - $window) AND ts <= $now
GROUP BY user_id
HAVING SUM(input_tokens + output_tokens + reasoning_tokens) > $budget
ORDER BY user_id;
