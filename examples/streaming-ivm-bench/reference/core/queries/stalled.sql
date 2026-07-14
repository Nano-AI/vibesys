-- Q4 stalled requests  --  non-monotonic ANTI-JOIN (NOT EXISTS) over a sliding window.
--
-- "Which failed/timed-out requests have NOT been followed by a success from the same user
--  within the last :window seconds?" A request enters the stalled set on failure and is
-- RETRACTED when a later success by the same user arrives (or when it ages out). This is
-- the retraction-native anti-join shape that append-only stream views mishandle.
--
-- key = request_id, value = NULL (pure membership). $now = snapshot event-time.
SELECT
    e.request_id
FROM events e
WHERE e.status <> 'success'
  AND e.ts > ($now - $window) AND e.ts <= $now
  AND NOT EXISTS (
        SELECT 1
        FROM events s
        WHERE s.user_id = e.user_id
          AND s.status = 'success'
          AND s.ts > e.ts
          AND s.ts > ($now - $window) AND s.ts <= $now
    )
ORDER BY e.request_id;
