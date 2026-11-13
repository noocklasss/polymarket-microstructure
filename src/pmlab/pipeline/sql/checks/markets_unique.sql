-- severity: error
-- Every market appears once.
SELECT slug, count(*) AS copies FROM raw_markets GROUP BY slug HAVING count(*) > 1;
