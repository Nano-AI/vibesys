-- Q2 active-user cardinality  --  non-monotonic via COUNT(DISTINCT) over a sliding window.
--
-- "How many DISTINCT users is each project serving in the last :window seconds?"
-- Sliding event-time window; the distinct count RETRACTS (drops) when a user's last
-- in-window event ages out. This is the query where approximate-distinct engines
-- (HyperLogLog materialized views) reveal value error against the exact oracle.
--
-- key = project_id, value = COUNT(DISTINCT user_id). All projects with >=1 in-window
-- event are emitted (no HAVING); accuracy is judged on the count value per snapshot.
-- $now is the snapshot event-time (never wall-clock).
SELECT
    project_id,
    COUNT(DISTINCT user_id) AS active_users
FROM events
WHERE ts > ($now - $window) AND ts <= $now
GROUP BY project_id
ORDER BY project_id;
