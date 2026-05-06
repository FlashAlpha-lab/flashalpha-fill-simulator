"""Quote-quality filter shared by entry and exit simulators.

Drops degenerate quotes that would produce phantom fills or trigger spurious
PT/SL transitions. Calibrated against 1-min SPY option chain data; the
fill_max_rel_spread default of 0.50 is generous and may need tightening for
less-liquid underlyings.
"""

from __future__ import annotations


def quote_passes_filter(
    bid: float | None,
    ask: float | None,
    max_rel_spread: float,
) -> bool:
    """Return True if (bid, ask) is a usable quote.

    Rejects:
    - missing values (None comes through as falsy via the explicit check)
    - non-positive bid or ask
    - crossed market (ask < bid)
    - excessive relative spread (ask - bid) / mid > max_rel_spread
    """
    if bid is None or ask is None:
        return False
    if bid <= 0 or ask <= 0:
        return False
    if ask < bid:
        return False
    mid = (bid + ask) / 2.0
    if mid > 0 and (ask - bid) / mid > max_rel_spread:
        return False
    return True


def mid_sanity(bid: float | None, ask: float | None) -> float | None:
    """Return mid price if the quote passes basic sanity, else None.

    Used by ``monitor_path``-style callers that need a mid value or skip
    the bar entirely. Looser than ``quote_passes_filter`` — does not check
    relative spread (callers wanting that should call both).
    """
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0
