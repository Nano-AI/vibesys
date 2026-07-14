-- Q3 top-k costliest models  --  non-monotonic ranking over a sliding window.
--
-- "Which :k models cost the most in the last :window seconds?" The ranked set RETRACTS
-- and reorders as costly requests age out of the window -- a model can drop out of the
-- top-k even though no new events removed it, purely from expiry. Classic non-monotonic
-- top-k that a naive append-only view gets wrong at the leave-edge.
--
-- key = model, value = cost in MICRO-dollars (integer, exact). cost_usd is defined by the
-- generator as PRICE_MILLI[model] * total_tokens / 1e6, so micro-dollars =
-- PRICE_MILLI[model] * total_tokens is exact integer arithmetic; oracle and the reference
-- maintainer therefore agree bit-for-bit (no float drift in the ranking). The CASE below
-- MUST mirror config.PRICE_MILLI. $now = snapshot event-time.
SELECT
    model,
    SUM(
        (input_tokens + output_tokens + reasoning_tokens) *
        CASE model
            WHEN 'gpt-4o'          THEN 5000
            WHEN 'claude-sonnet-5' THEN 3000
            WHEN 'claude-opus-4-8' THEN 15000
            WHEN 'gpt-4o-mini'     THEN 600
            WHEN 'llama-3-70b'     THEN 900
        END
    ) AS cost_micro
FROM events
WHERE ts > ($now - $window) AND ts <= $now
GROUP BY model
ORDER BY cost_micro DESC, model
LIMIT $k;
