"""Tests for the ``simulate_fills`` loop-driving wrapper.

Confirms the wrapper correctly integrates ``simulate_fill`` against an
``InMemoryChainProvider`` over a multi-bar wait window.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fillsim import Config, Leg, Spread, simulate_fills
from tests.fixtures.synthetic_chains import make_provider

EXPIRY = date(2026, 5, 15)
POSTED = datetime(2026, 4, 15, 10, 5)


def _spread(short_strike: float, long_strike: float, limit: float) -> Spread:
    return Spread(
        short=Leg(strike=short_strike, bid=0.0, ask=0.0),
        long=Leg(strike=long_strike, bid=0.0, ask=0.0),
        limit_credit=limit,
        width=short_strike - long_strike,
        expiry=EXPIRY,
    )


def test_no_chain_data_returns_no_fill():
    cand = _spread(440, 435, 0.40)
    provider = make_provider(POSTED, EXPIRY, [])
    result = simulate_fills(POSTED, [cand], provider)
    assert not result.filled
    assert result.bars_waited == 0


def test_fills_on_first_bar_that_crosses():
    cand = _spread(440, 435, 0.40)
    cfg = Config(min_edge_floor=-0.10)  # permissive — isolate loop behavior
    # bar 0 (posted_ts itself) — outside window (start_offset_bars=1 by default)
    # bar 1: combo_bid = 1.40 - 0.95 = 0.45 ≥ 0.42 ✓; mid edge = -0.06 > -0.10 ✓ → fill
    legs_per_bar = [
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 0 (skipped)
        [(440.0, 1.40, 1.40), (435.0, 0.93, 0.95)],   # bar 1: fill here
        [(440.0, 1.50, 1.55), (435.0, 0.85, 0.90)],   # bar 2 (not reached)
    ]
    provider = make_provider(POSTED, EXPIRY, legs_per_bar)
    result = simulate_fills(POSTED, [cand], provider, cfg)
    assert result.filled
    assert result.fill is not None
    assert result.fill.fill_ts == POSTED + timedelta(minutes=1)
    assert result.fill.fill_price == pytest.approx(0.40)


def test_no_fill_within_wait_window_returns_unfilled_with_bars_walked():
    cand = _spread(440, 435, 0.40)
    cfg = Config(fill_max_wait_bars=3)
    legs_per_bar = [
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 0
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 1
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 2
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 3
        [(440.0, 1.20, 1.25), (435.0, 0.85, 0.90)],   # bar 4
    ]
    provider = make_provider(POSTED, EXPIRY, legs_per_bar)
    result = simulate_fills(POSTED, [cand], provider, cfg)
    assert not result.filled
    # window = bars 1..3 (start_offset=1, max_wait=3) → 3 bars walked
    assert result.bars_waited == 3


def test_near_misses_accumulate_across_bars_before_fill():
    cand = _spread(440, 435, 0.40)
    cfg = Config(min_edge_floor=-0.10)
    # Bar 1: combo_bid = 1.30 - 0.90 = 0.40 == limit → near miss
    # Bar 2: combo_bid = 1.30 - 0.90 = 0.40 → near miss
    # Bar 3: combo_bid = 1.40 - 0.95 = 0.45 → fill (mid=0.46, edge=-0.06 > -0.10)
    legs_per_bar = [
        [(440.0, 1.25, 1.30), (435.0, 0.85, 0.90)],   # bar 0 (skipped)
        [(440.0, 1.30, 1.30), (435.0, 0.85, 0.90)],   # bar 1: NM
        [(440.0, 1.30, 1.30), (435.0, 0.85, 0.90)],   # bar 2: NM
        [(440.0, 1.40, 1.40), (435.0, 0.93, 0.95)],   # bar 3: FILL
    ]
    provider = make_provider(POSTED, EXPIRY, legs_per_bar)
    result = simulate_fills(POSTED, [cand], provider, cfg)
    assert result.filled
    assert result.near_misses == 2
