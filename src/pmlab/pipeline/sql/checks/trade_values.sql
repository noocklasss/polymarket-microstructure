-- severity: error
-- Trade prints have a side, a price in (0, 1] and a positive size.
SELECT slug, seq, side, price, size
FROM raw_trades
WHERE side NOT IN ('up', 'down') OR side IS NULL
   OR price IS NULL OR price <= 0 OR price > 1
   OR size IS NULL OR size <= 0;
