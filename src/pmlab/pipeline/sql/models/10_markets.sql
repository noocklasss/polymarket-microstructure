-- One row per recorded market, with its final result when known.
-- winner is 'up' / 'down' only for a final result (payouts exactly 1 and 0); anything else is NULL.
CREATE OR REPLACE TABLE markets AS
SELECT
    m.family,
    m.day,
    m.slug,
    to_timestamp(m.start) AS start_at,
    m.start,
    m.duration_s,
    m.condition_id,
    m.question,
    m.tick_size,
    o.payout_up,
    o.payout_down,
    CASE
        WHEN o.payout_up = 1 AND o.payout_down = 0 THEN 'up'
        WHEN o.payout_up = 0 AND o.payout_down = 1 THEN 'down'
    END AS winner,
    m.lines,
    m.bad_lines,
    m.file_bytes
FROM raw_markets AS m
LEFT JOIN raw_outcomes AS o USING (condition_id);
