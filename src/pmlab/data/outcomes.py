"""Official market results: condition id -> payouts ``[UP, DOWN]`` (1 for the winner, 0 for the loser once final).

Only a final result counts. A market that is still settling shows prices such as 0.995 / 0.005 for a while; using those
as the winner would let a backtest score markets whose result was not yet official.
"""

from __future__ import annotations

import json
from pathlib import Path

UP, DOWN = 0, 1


def load_outcomes(path: Path) -> dict[str, list[float]]:
    return {cid: [float(x) for x in pay] for cid, pay in json.loads(Path(path).read_text(encoding="utf-8")).items()}


def winner_of(payouts) -> int | None:
    """0 = UP won, 1 = DOWN won, None if the result is missing or not final."""
    if not payouts or len(payouts) != 2 or max(payouts) != 1 or min(payouts) != 0:
        return None
    return UP if payouts[0] == 1 else DOWN
