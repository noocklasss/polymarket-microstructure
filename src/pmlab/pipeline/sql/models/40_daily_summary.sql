-- One row per family and day: how much was recorded, how good it was, and how markets resolved.
CREATE OR REPLACE TABLE daily_summary AS
SELECT
    family,
    day,
    count(*) AS markets,
    count(winner) AS with_result,
    avg(CASE WHEN winner = 'up' THEN 1.0 WHEN winner = 'down' THEN 0.0 END) AS up_share,
    sum(trades) AS trades,
    round(sum(volume_usd), 2) AS volume_usd,
    median(two_sided_coverage) AS median_coverage,
    count(*) FILTER (WHERE two_sided_coverage >= 0.8) AS good_recordings
FROM market_summary
GROUP BY family, day
ORDER BY family, day;
