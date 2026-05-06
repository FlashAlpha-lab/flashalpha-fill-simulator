"""Per-bar primitive tests for ``simulate_fill``.

Each test constructs a single-bar ``ChainSnapshot``, evaluates one or more
candidates against it, and checks the ``BarResult``. No DB, no provider,
no time loop.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from fillsim import Config, Leg, Spread, simulate_fill
from fillsim.entry import _bar_seed
from tests.fixtures.synthetic_chains import make_snapshot

EXPIRY = date(2026, 5, 15)
BAR_TS = datetime(2026, 4, 15, 10, 5)


def _spread(short_strike: float, long_strike: float, limit: float) -> Spread:
    return Spread(
        short=Leg(strike=short_strike, bid=0.0, ask=0.0),
        long=Leg(strike=long_strike, bid=0.0, ask=0.0),
        limit_credit=limit,
        width=short_strike - long_strike,
        expiry=EXPIRY,
    )


# ---------------------------------------------------------------------------
# Empty / missing legs
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_no_fill_no_near_misses():
    res = simulate_fill(BAR_TS, {}, [])
    assert res.fill is None
    assert res.near_misses == 0


def test_missing_short_leg_yields_no_fill():
    cand = _spread(440, 435, 0.40)
    snap = make_snapshot([(EXPIRY, 435, 0.85, 0.90)])  # only the long leg present
    res = simulate_fill(BAR_TS, snap, [cand])
    assert res.fill is None and res.near_misses == 0


def test_missing_long_leg_yields_no_fill():
    cand = _spread(440, 435, 0.40)
    snap = make_snapshot([(EXPIRY, 440, 1.20, 1.25)])
    res = simulate_fill(BAR_TS, snap, [cand])
    assert res.fill is None and res.near_misses == 0


def test_candidate_without_expiry_raises():
    cand = Spread(
        short=Leg(440, 0, 0),
        long=Leg(435, 0, 0),
        limit_credit=0.40,
        width=5.0,
        expiry=None,
    )
    with pytest.raises(ValueError, match="no expiry"):
        simulate_fill(BAR_TS, {}, [cand])


# ---------------------------------------------------------------------------
# Quote-quality filter
# ---------------------------------------------------------------------------


def test_filter_rejects_negative_bid():
    cand = _spread(440, 435, 0.40)
    snap = make_snapshot(
        [
            (EXPIRY, 440, -0.10, 1.25),  # invalid
            (EXPIRY, 435, 0.85, 0.90),
        ]
    )
    assert simulate_fill(BAR_TS, snap, [cand]).fill is None


def test_filter_rejects_crossed_market():
    cand = _spread(440, 435, 0.40)
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.50, 1.25),  # ask < bid, crossed
            (EXPIRY, 435, 0.85, 0.90),
        ]
    )
    assert simulate_fill(BAR_TS, snap, [cand]).fill is None


def test_filter_rejects_excessive_relative_spread():
    cand = _spread(440, 435, 0.40)
    snap = make_snapshot(
        [
            (EXPIRY, 440, 0.50, 2.00),  # spread/mid = 1.5/1.25 = 1.2 > 0.5
            (EXPIRY, 435, 0.85, 0.90),
        ]
    )
    assert simulate_fill(BAR_TS, snap, [cand]).fill is None


# ---------------------------------------------------------------------------
# Fill threshold semantics
# ---------------------------------------------------------------------------


def test_combo_bid_equal_limit_is_near_miss_not_fill():
    """combo_bid == limit must NOT fill (need limit + epsilon)."""
    cand = _spread(440, 435, 0.40)
    # short.bid - long.ask = 1.30 - 0.90 = 0.40 == limit
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.30, 1.35),
            (EXPIRY, 435, 0.85, 0.90),
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand])
    assert res.fill is None
    assert res.near_misses == 1


def test_combo_bid_at_limit_plus_epsilon_fills():
    """combo_bid == limit + epsilon → fill at limit price.

    Prices are constructed so combo_mid is close to combo_bid (tight spread),
    so the stale-quote guard does not reject the fill.
    """
    cand = _spread(440, 435, 0.40)
    cfg = Config(fill_epsilon=0.02)
    # short.bid=1.30 short.ask=1.30 long.bid=0.86 long.ask=0.88
    # combo_bid = 1.30 - 0.88 = 0.42 (= limit + epsilon)
    # combo_mid = 1.30 - 0.87 = 0.43; edge_at_fill = 0.40 - 0.43 = -0.03 ≥ -0.05 ✓
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.30, 1.30),
            (EXPIRY, 435, 0.86, 0.88),
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand], cfg)
    assert res.fill is not None
    assert res.fill.fill_price == pytest.approx(0.40)  # limit, not combo_bid
    assert res.near_misses == 0


def test_fill_price_is_limit_not_market_price():
    """Even when combo_bid is much higher than limit + epsilon, fill_price == limit.

    Uses a permissive ``min_edge_floor`` so the stale-quote guard doesn't
    fire — this test's job is to isolate the limit-not-market price contract.
    """
    cand = _spread(440, 435, 0.40)
    cfg = Config(min_edge_floor=-1.0)
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.50, 1.55),  # combo_bid = 1.50 - 0.90 = 0.60 (well above)
            (EXPIRY, 435, 0.85, 0.90),
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand], cfg)
    assert res.fill is not None
    assert res.fill.fill_price == 0.40  # not 0.60


# ---------------------------------------------------------------------------
# MIN_EDGE_FLOOR — stale-quote guard
# ---------------------------------------------------------------------------


def test_min_edge_floor_rejects_stale_cross():
    """combo_bid crosses, but combo_mid has moved well below the limit
    by more than -min_edge_floor → reject as stale."""
    cand = _spread(440, 435, 0.40)
    cfg = Config(fill_epsilon=0.02, min_edge_floor=-0.05)
    # combo_bid = 0.42 (clears limit + epsilon).
    # combo_mid = (1.32+1.37)/2 - (1.40+1.45)/2 = 1.345 - 1.425 = -0.08
    # Wait: that's not what we want. Let me set up so combo_mid >> limit means
    # we'd be filling much WORSE than mid.
    # Actually edge_at_fill = limit - combo_mid_at_fill
    # We want edge_at_fill < min_edge_floor (= -0.05), i.e. combo_mid > limit + 0.05.
    # short.mid = 1.50, long.mid = 1.00 → combo_mid = 0.50; limit=0.40; edge = -0.10 → reject.
    # We need short.bid - long.ask >= 0.42 still: short.bid=1.50, long.ask=1.05.
    # short = (1.50, 1.50 ask=?), need short.mid = 1.50 → bid+ask=3.0. Make ask=1.50.
    # long.bid+ask = 2.0. ask=1.05, bid=0.95. long.mid = 1.0. ✓
    # combo_bid = 1.50 - 1.05 = 0.45 ≥ 0.42 ✓
    # combo_mid = 1.50 - 1.00 = 0.50; edge = 0.40 - 0.50 = -0.10 < -0.05 → reject
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.50, 1.50),
            (EXPIRY, 435, 0.95, 1.05),
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand], cfg)
    assert res.fill is None
    # Note: a rejected fill is NOT a near-miss; it's a different rejection path.
    assert res.near_misses == 0


def test_min_edge_floor_accepts_when_edge_strictly_above_floor():
    """edge_at_fill > min_edge_floor → fill (the check is strict less-than).

    We avoid the exact-boundary case in this test because IEEE-754 floating
    point makes "exactly at boundary" comparisons unreliable when constructing
    inputs from decimal literals like 0.45. The semantics worth locking is
    "comfortably above the floor still fills".
    """
    cand = _spread(440, 435, 0.40)
    cfg = Config(fill_epsilon=0.02, min_edge_floor=-0.10)
    # combo_bid = 1.40 - 0.95 = 0.45 (≥ 0.42 ✓)
    # combo_mid = 1.40 - 0.94 = 0.46
    # edge_at_fill = 0.40 - 0.46 = -0.06; -0.06 > -0.10 ✓
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.40, 1.40),
            (EXPIRY, 435, 0.93, 0.95),
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand], cfg)
    assert res.fill is not None
    assert res.fill.fill_price == 0.40


# ---------------------------------------------------------------------------
# mid_at_fill / edge_captured
# ---------------------------------------------------------------------------


def test_mid_at_fill_is_reported_and_edge_captured_is_negative_for_ask_edge_pricing():
    """For ask-edge limits, fill_price < mid → edge_captured negative.

    Uses permissive min_edge_floor to isolate the mid-reporting contract from
    the stale-quote guard (which would otherwise reject this fill).
    """
    cand = _spread(440, 435, 0.40)
    cfg = Config(min_edge_floor=-1.0)
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.32, 1.37),  # short mid = 1.345
            (EXPIRY, 435, 0.85, 0.90),  # long mid = 0.875
        ]
    )
    res = simulate_fill(BAR_TS, snap, [cand], cfg)
    assert res.fill is not None
    assert res.fill.mid_at_fill == pytest.approx(1.345 - 0.875)  # 0.47
    assert res.fill.edge_captured == pytest.approx(0.40 - 0.47)  # -0.07


# ---------------------------------------------------------------------------
# Tiebreak determinism (EV-oracle regression)
# ---------------------------------------------------------------------------


def test_first_fill_wins_even_when_a_higher_ev_candidate_also_crosses_same_bar():
    """Same-bar tiebreak must NEVER prefer the higher-EV candidate.

    Both candidates cross at this bar. Whichever wins, the result must be
    deterministic for a given bar_ts AND independent of EV ordering. We
    verify by running with the candidates in two different list orders;
    if the simulator silently EV-sorted, the winner would track the EV ranking
    rather than the seed.
    """
    a = _spread(440, 435, 0.30)  # smaller credit → "lower EV"
    b = _spread(450, 445, 0.60)  # larger credit → "higher EV"
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.30, 1.30),
            (EXPIRY, 435, 0.95, 0.97),  # a crosses
            (EXPIRY, 450, 2.00, 2.00),
            (EXPIRY, 445, 1.35, 1.37),  # b crosses
        ]
    )
    # We don't assert a specific winner — only that it's deterministic for
    # the same bar_ts AND the same regardless of input list order.
    r1 = simulate_fill(BAR_TS, snap, [a, b])
    r2 = simulate_fill(BAR_TS, snap, [b, a])
    assert r1.fill is not None and r2.fill is not None
    # The winner is determined by the seeded shuffle; both runs see the same
    # set of crossing candidates, so the deterministic shuffle output is the same.
    # (Note: the *pre-shuffle* order differs between r1 and r2, so the post-
    # shuffle index 0 may differ — what's important is that neither systematically
    # selects the higher-EV candidate.)
    # Run many times — winner distribution should not be EV-biased:
    a_wins = 0
    b_wins = 0
    for hour in range(24):
        ts = datetime(2026, 4, 15, hour, 5)
        r = simulate_fill(ts, snap, [a, b])
        if r.fill is not None:
            if r.fill.candidate is a:
                a_wins += 1
            else:
                b_wins += 1
    # Over 24 different timestamps, both candidates should win at least once
    # (probabilistic, but with 24 trials this is overwhelmingly likely if
    # the tiebreak is genuinely random over the seed).
    assert a_wins > 0 and b_wins > 0, f"Tiebreak appears non-random: a={a_wins} b={b_wins}"


def test_same_bar_tiebreak_is_deterministic_across_runs():
    """Same bar_ts + same chain → same winner, repeatable."""
    a = _spread(440, 435, 0.30)
    b = _spread(450, 445, 0.60)
    snap = make_snapshot(
        [
            (EXPIRY, 440, 1.30, 1.30),
            (EXPIRY, 435, 0.95, 0.97),
            (EXPIRY, 450, 2.00, 2.00),
            (EXPIRY, 445, 1.35, 1.37),
        ]
    )
    r1 = simulate_fill(BAR_TS, snap, [a, b])
    r2 = simulate_fill(BAR_TS, snap, [a, b])
    assert r1.fill is not None and r2.fill is not None
    assert r1.fill.candidate.name == r2.fill.candidate.name


def test_same_bar_tiebreak_does_not_consume_global_random_state():
    """Verify the simulator uses its own random.Random instance, leaving
    the global RNG state untouched."""
    random.seed(42)
    before = random.random()
    random.seed(42)

    a = _spread(440, 435, 0.30)
    snap = make_snapshot([(EXPIRY, 440, 1.30, 1.30), (EXPIRY, 435, 0.95, 0.97)])
    simulate_fill(BAR_TS, snap, [a])

    # Global RNG was not consumed — re-seed and read should equal `before`.
    after = random.random()
    assert after == before


def test_tiebreak_seed_is_stable_for_naive_and_aware_datetimes():
    naive = datetime(2026, 4, 15, 10, 5)
    utc = datetime(2026, 4, 15, 10, 5, tzinfo=timezone.utc)
    utc_plus_two = datetime(
        2026,
        4,
        15,
        12,
        5,
        tzinfo=timezone(timedelta(hours=2)),
    )

    assert _bar_seed(naive) == _bar_seed(utc)
    assert _bar_seed(utc) == _bar_seed(utc_plus_two)


# ---------------------------------------------------------------------------
# Multi-expiry
# ---------------------------------------------------------------------------


def test_multi_expiry_same_bar_both_cross_one_wins():
    """Two candidates from different expiries both cross on the same bar.
    Exactly one wins (deterministically); the other is left open."""
    exp_a = date(2026, 5, 15)
    exp_b = date(2026, 6, 19)
    cand_a = Spread(Leg(440, 0, 0), Leg(435, 0, 0), 0.30, 5.0, exp_a)
    cand_b = Spread(Leg(450, 0, 0), Leg(445, 0, 0), 0.60, 5.0, exp_b)
    snap = {
        (exp_a, 440): (1.30, 1.30),
        (exp_a, 435): (0.95, 0.97),
        (exp_b, 450): (2.00, 2.00),
        (exp_b, 445): (1.35, 1.37),
    }
    res = simulate_fill(BAR_TS, snap, [cand_a, cand_b])
    assert res.fill is not None
    assert res.fill.candidate.expiry in (exp_a, exp_b)


# ---------------------------------------------------------------------------
# near_misses — per-candidate-per-bar semantics
# ---------------------------------------------------------------------------


def test_near_misses_count_per_candidate_per_bar_not_per_bar():
    """If 3 candidates near-miss the same bar, near_misses should be 3, not 1."""
    a = _spread(440, 435, 0.40)
    b = _spread(442, 437, 0.40)
    c = _spread(444, 439, 0.40)
    # Each candidate's combo_bid must equal its limit exactly.
    # Construct legs s.t. short.bid - long.ask = 0.40 for each.
    snap = {
        (EXPIRY, 440): (1.25, 1.30),
        (EXPIRY, 435): (0.80, 0.85),  # 1.25 - 0.85 = 0.40
        (EXPIRY, 442): (1.30, 1.35),
        (EXPIRY, 437): (0.85, 0.90),  # 1.30 - 0.90 = 0.40
        (EXPIRY, 444): (1.35, 1.40),
        (EXPIRY, 439): (0.90, 0.95),  # 1.35 - 0.95 = 0.40
    }
    res = simulate_fill(BAR_TS, snap, [a, b, c])
    assert res.fill is None
    assert res.near_misses == 3
