"""Train / validation / exam split in time order.

Markets are never shuffled: the earliest block is for searching, the next for choosing among the shortlisted versions,
and the latest block (the exam) is opened once, at the end. Splits fall on whole days (New York time) so that no day is
partly in one block and partly in another.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def market_day(start: int) -> str:
    return datetime.fromtimestamp(start, ET).strftime("%Y-%m-%d")


def time_split(starts, fractions=(0.6, 0.2, 0.2), names=("train", "valid", "exam")) -> dict[str, list[int]]:
    """Split market start times into consecutive blocks of whole days holding about ``fractions`` of the markets."""
    if len(fractions) != len(names) or abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("fractions must match names and sum to 1")
    starts = sorted(starts)
    days: dict[str, list[int]] = {}
    for s in starts:
        days.setdefault(market_day(s), []).append(s)
    blocks: dict[str, list[int]] = {n: [] for n in names}
    total, seen, edges, acc = len(starts), 0, [], 0.0
    for f in fractions[:-1]:
        acc += f
        edges.append(acc * total)
    block = 0
    for day in sorted(days):
        while block < len(edges) and seen >= edges[block]:
            block += 1
        blocks[names[block]].extend(days[day])
        seen += len(days[day])
    return blocks
