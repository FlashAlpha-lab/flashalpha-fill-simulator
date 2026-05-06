"""ChainProvider Protocol and the shared Quote dataclass.

A provider is anything with ``get_quotes()`` and ``get_spot()``. Structural
typing (PEP 544) — no need to subclass.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class Quote:
    """A single option quote at a single point in time."""

    ts: datetime
    expiry: date
    strike: float
    right: Literal["PUT", "CALL"]
    bid: float
    ask: float


@runtime_checkable
class ChainProvider(Protocol):
    """Interface a chain provider must implement.

    Two methods. Both should be efficient enough to call repeatedly in a
    backtest loop — providers are responsible for their own caching strategy.
    """

    def get_quotes(
        self,
        start_ts: datetime,
        end_ts: datetime,
        expiry: date,
        strikes: list[float],
        right: Literal["PUT", "CALL"] = "PUT",
    ) -> Iterable[Quote]:
        """Return all quotes matching (expiry, right, strike in strikes) with
        ``ts`` in ``[start_ts, end_ts]``.

        Order does not matter — the simulator sorts internally. Providers
        should NOT pre-filter for quote sanity; the simulator does that.
        """
        ...

    def get_spot(self, ts: datetime, symbol: str = "SPY") -> float | None:
        """Underlying mid-quote at ``ts``. Used for expiry settlement only.

        Return ``None`` if the symbol/ts combination has no data; the
        simulator will fall back to nearby timestamps before aborting.
        """
        ...
