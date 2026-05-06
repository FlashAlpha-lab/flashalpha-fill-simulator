"""In-memory chain provider for tests and small offline runs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal

from fillsim.providers.base import Quote


class InMemoryChainProvider:
    """A ChainProvider backed by a list of ``Quote`` objects in memory.

    No external dependencies. Suitable for tests, examples, and small
    offline backtests where the entire chain fits in RAM.
    """

    def __init__(
        self,
        quotes: Iterable[Quote] | None = None,
        spots: dict[datetime, float] | None = None,
    ) -> None:
        self._quotes: list[Quote] = list(quotes) if quotes is not None else []
        self._spots: dict[datetime, float] = dict(spots) if spots is not None else {}

    def add_quote(self, q: Quote) -> None:
        self._quotes.append(q)

    def add_quotes(self, qs: Iterable[Quote]) -> None:
        self._quotes.extend(qs)

    def add_spot(self, ts: datetime, price: float) -> None:
        self._spots[ts] = price

    def get_quotes(
        self,
        start_ts: datetime,
        end_ts: datetime,
        expiry: date,
        strikes: list[float],
        right: Literal["PUT", "CALL"] = "PUT",
    ) -> Iterable[Quote]:
        sset = set(strikes)
        for q in self._quotes:
            if q.right != right:
                continue
            if q.expiry != expiry:
                continue
            if q.strike not in sset:
                continue
            if not (start_ts <= q.ts <= end_ts):
                continue
            yield q

    def get_spot(self, ts: datetime, symbol: str = "SPY") -> float | None:
        return self._spots.get(ts)
