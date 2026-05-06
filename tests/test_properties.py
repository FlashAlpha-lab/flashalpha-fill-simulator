"""Property-based invariants for the simulator's pure functions."""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")

from fillsim import expiry_settlement_pnl  # noqa: E402
from fillsim.filters import quote_passes_filter  # noqa: E402


@hypothesis.given(
    long_strike=st.floats(min_value=1.0, max_value=1_000.0, allow_nan=False),
    width=st.floats(min_value=0.5, max_value=100.0, allow_nan=False),
    entry_credit=st.floats(min_value=0.01, max_value=50.0, allow_nan=False),
    spot_offset=st.floats(min_value=-150.0, max_value=150.0, allow_nan=False),
)
@hypothesis.settings(max_examples=75)
def test_put_credit_settlement_is_bounded_by_max_profit_and_loss(
    long_strike: float,
    width: float,
    entry_credit: float,
    spot_offset: float,
):
    short_strike = long_strike + width
    spot = long_strike + spot_offset

    pnl = expiry_settlement_pnl(spot, short_strike, long_strike, width, entry_credit)

    assert entry_credit - width <= pnl <= entry_credit


@hypothesis.given(
    bid=st.one_of(st.none(), st.floats(max_value=0.0, allow_nan=False)),
    ask=st.floats(min_value=0.01, max_value=100.0, allow_nan=False),
)
@hypothesis.settings(max_examples=50)
def test_quote_filter_never_accepts_missing_or_non_positive_bids(
    bid: float | None,
    ask: float,
):
    assert not quote_passes_filter(bid, ask, 0.50)


@hypothesis.given(
    bid=st.floats(min_value=0.01, max_value=100.0, allow_nan=False),
    ask=st.floats(min_value=0.01, max_value=100.0, allow_nan=False),
)
@hypothesis.settings(max_examples=50)
def test_quote_filter_never_accepts_crossed_markets(bid: float, ask: float):
    if ask < bid:
        assert not quote_passes_filter(bid, ask, 0.50)
