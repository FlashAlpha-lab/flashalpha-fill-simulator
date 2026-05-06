"""Synthetic chain builders for tests.

Tiny helpers to produce ChainSnapshot dicts and InMemoryChainProvider
instances at well-defined prices. Tests should be readable in isolation:
each builder call should make the test scenario self-evident.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from fillsim import InMemoryChainProvider, Quote
from fillsim.core import ChainSnapshot


def make_snapshot(
    legs: Iterable[tuple[date, float, float, float]]
) -> ChainSnapshot:
    """Build a ChainSnapshot from (expiry, strike, bid, ask) tuples.

    >>> snap = make_snapshot([
    ...     (date(2026,5,15), 440.0, 1.20, 1.25),
    ...     (date(2026,5,15), 435.0, 0.85, 0.90),
    ... ])
    >>> snap[(date(2026,5,15), 440.0)]
    (1.2, 1.25)
    """
    return {(exp, strike): (bid, ask) for exp, strike, bid, ask in legs}


def make_quotes(
    posted_ts: datetime,
    expiry: date,
    legs_per_bar: list[list[tuple[float, float, float]]],
    *,
    bar_step: timedelta = timedelta(minutes=1),
    right: str = "PUT",
) -> list[Quote]:
    """Build a list of Quote objects from per-bar leg specs.

    ``legs_per_bar[i]`` is the list of ``(strike, bid, ask)`` tuples for
    bar ``i`` (0-indexed from ``posted_ts``). Bars where a strike is missing
    will have no quote for it (legitimate "leg unavailable").
    """
    out: list[Quote] = []
    for i, bar_legs in enumerate(legs_per_bar):
        ts = posted_ts + bar_step * i
        for strike, bid, ask in bar_legs:
            out.append(
                Quote(
                    ts=ts,
                    expiry=expiry,
                    strike=strike,
                    right=right,  # type: ignore[arg-type]
                    bid=bid,
                    ask=ask,
                )
            )
    return out


def make_provider(
    posted_ts: datetime,
    expiry: date,
    legs_per_bar: list[list[tuple[float, float, float]]],
    *,
    bar_step: timedelta = timedelta(minutes=1),
) -> InMemoryChainProvider:
    """Convenience: build an InMemoryChainProvider from per-bar leg specs."""
    return InMemoryChainProvider(
        quotes=make_quotes(posted_ts, expiry, legs_per_bar, bar_step=bar_step)
    )
