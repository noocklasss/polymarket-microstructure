-- severity: error
-- No market from before the research window (1 Aug 2026, 00:00 New York = 1785556800).
SELECT slug, start FROM raw_markets WHERE start < 1785556800;
