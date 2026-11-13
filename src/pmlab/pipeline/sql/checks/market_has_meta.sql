-- severity: error
-- Every recording starts with its market description (needed to join the result).
SELECT slug, file_name FROM raw_markets WHERE condition_id IS NULL;
