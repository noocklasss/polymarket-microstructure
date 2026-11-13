-- severity: warn
-- Recordings where both sides had a sane book at most 5 seconds old for less than 80% of the market. Stricter than
-- the research quality audit (pmlab.data.quality), which also counts a side whose book was recorded as empty.
SELECT slug, round(two_sided_coverage, 3) AS two_sided_coverage
FROM market_summary
WHERE two_sided_coverage IS NULL OR two_sided_coverage < 0.8;
