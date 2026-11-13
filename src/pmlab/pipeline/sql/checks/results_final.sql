-- severity: warn
-- Markets that ended more than a day before the newest recorded market but still have no final result.
SELECT slug, start_at, payout_up, payout_down
FROM markets
WHERE winner IS NULL
  AND start + duration_s < (SELECT max(start) FROM markets) - 86400;
