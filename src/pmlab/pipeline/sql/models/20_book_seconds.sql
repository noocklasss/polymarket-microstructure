-- The as-of top of book for every whole second of every market, per side: the same array as
-- pmlab.data.loaders.load_books (tests/test_pipeline.py checks they agree value for value).
--
-- * Only book changes from one market length before the start to one market length after it are used.
-- * Several changes at the same instant: the one written last wins, as when replaying the file in order.
-- * A change that does not form a sane book (bid <= ask, prices in [0, 1], ask > 0, sizes >= 0) still replaces
--   the previous one, but as an unknown book (NULLs): the loader treats it the same way.
-- * seen_at is when the book in force was recorded, so age = second - seen_at says how stale it is.
CREATE OR REPLACE TABLE book_seconds AS
WITH changes AS (
    SELECT b.slug, b.side, b.t, b.best_bid, b.best_ask, b.best_bid_size, b.best_ask_size
    FROM raw_book AS b
    JOIN markets AS m USING (slug)
    WHERE b.side IN ('up', 'down')
      AND b.t >= -m.duration_s AND b.t < m.duration_s
      AND b.best_bid IS NOT NULL AND b.best_ask IS NOT NULL
      AND b.best_bid_size IS NOT NULL AND b.best_ask_size IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY b.slug, b.side, b.t ORDER BY b.seq DESC) = 1
),
checked AS (
    SELECT
        slug, side, t,
        (0 <= best_bid AND best_bid <= best_ask AND best_ask <= 1 AND best_ask > 0
         AND best_bid_size >= 0 AND best_ask_size >= 0) AS sane,
        best_bid, best_ask, best_bid_size, best_ask_size
    FROM changes
),
grid AS (
    SELECT m.family, m.day, m.slug, s.side, unnest(range(m.duration_s)) AS second
    FROM markets AS m
    CROSS JOIN (VALUES ('up'), ('down')) AS s(side)
)
SELECT
    g.family,
    g.day,
    g.slug,
    g.side,
    g.second,
    CASE WHEN c.sane THEN c.best_bid END AS bid,
    CASE WHEN c.sane THEN c.best_ask END AS ask,
    CASE WHEN c.sane THEN c.best_bid_size END AS bid_size,
    CASE WHEN c.sane THEN c.best_ask_size END AS ask_size,
    CASE WHEN c.sane THEN c.t END AS seen_at,
    CASE WHEN c.sane THEN g.second - c.t END AS age
FROM grid AS g
ASOF LEFT JOIN checked AS c
    ON g.slug = c.slug AND g.side = c.side AND g.second >= c.t;
