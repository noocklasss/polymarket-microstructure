-- One row per market: trading activity, book quality and the result.
-- two_sided_coverage: share of the market's seconds where both sides had a sane book at most 5 seconds old.
CREATE OR REPLACE TABLE market_summary AS
WITH trade_stats AS (
    SELECT
        slug,
        count(*) AS trades,
        sum(size) AS volume_shares,
        sum(price * size) AS volume_usd,
        sum(price * size) FILTER (WHERE side = 'up') / nullif(sum(size) FILTER (WHERE side = 'up'), 0) AS vwap_up,
        sum(price * size) FILTER (WHERE side = 'down') / nullif(sum(size) FILTER (WHERE side = 'down'), 0) AS vwap_down
    FROM raw_trades
    WHERE t >= 0
    GROUP BY slug
),
book_stats AS (
    SELECT
        slug,
        avg(CASE WHEN up_fresh AND down_fresh THEN 1.0 ELSE 0.0 END) AS two_sided_coverage,
        median(up_spread) AS median_spread_up,
        arg_max(up_mid, second) FILTER (WHERE up_fresh) AS last_mid_up
    FROM (
        SELECT
            slug,
            second,
            bool_or(side = 'up' AND age <= 5) AS up_fresh,
            bool_or(side = 'down' AND age <= 5) AS down_fresh,
            round(max(ask - bid) FILTER (WHERE side = 'up' AND age <= 5), 4) AS up_spread,
            max((ask + bid) / 2) FILTER (WHERE side = 'up' AND age <= 5) AS up_mid
        FROM book_seconds
        GROUP BY slug, second
    )
    GROUP BY slug
)
SELECT
    m.family,
    m.day,
    m.slug,
    m.start_at,
    m.winner,
    coalesce(t.trades, 0) AS trades,
    coalesce(t.volume_shares, 0) AS volume_shares,
    coalesce(t.volume_usd, 0) AS volume_usd,
    t.vwap_up,
    t.vwap_down,
    b.two_sided_coverage,
    b.median_spread_up,
    b.last_mid_up
FROM markets AS m
LEFT JOIN trade_stats AS t USING (slug)
LEFT JOIN book_stats AS b USING (slug);
