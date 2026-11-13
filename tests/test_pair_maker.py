from pmlab.strategies.pair_maker import PairMaker

UP, DOWN = 0, 1


def state(up=(0.48, 0.50), down=(0.48, 0.50), sh=(0, 0), cost=(0, 0)):
    return dict(t=10, book={UP: [*up, 100, 100], DOWN: [*down, 100, 100]}, sh=list(sh), cost=list(cost))


def test_opens_only_on_a_side_cheap_enough_one_cent_above_its_best_bid():
    # Nothing held: the other side counts as 50c, so a side opens only at <= 47c (47 + 50 <= 97).
    assert PairMaker().step(state()) == ([], [])  # 49c + 50c > 97c on both sides
    bids, _ = PairMaker().step(state(up=(0.40, 0.42), down=(0.58, 0.60)))
    assert bids == [(UP, 0.41, 10)]


def test_joins_the_best_bid_when_the_spread_is_one_cent():
    bids, _ = PairMaker().step(state(up=(0.41, 0.42), down=(0.58, 0.59)))
    assert bids == [(UP, 0.41, 10)]


def test_heavier_side_stops_when_the_pair_would_cost_too_much():
    # 20 UP bought at 50c, DOWN at 48c on average. Another UP at 49c keeps UP's average near 49.7c,
    # and 49.7 + 48 > 97.
    bids, _ = PairMaker().step(state(sh=(20, 10), cost=(10.0, 4.8)))
    assert bids == [(DOWN, 0.49, 10)]  # only the lighter side, which completes pairs


def test_lopsided_side_is_capped():
    bids, _ = PairMaker(pair_max=2.0).step(state(sh=(70, 30), cost=(30.0, 10.0)))  # 70 > 1.3 x 30 + 30
    assert [s for s, _, _ in bids] == [DOWN]


def test_budget_stops_everything():
    assert PairMaker().step(state(sh=(300, 300), cost=(150.0, 150.0))) == ([], [])
