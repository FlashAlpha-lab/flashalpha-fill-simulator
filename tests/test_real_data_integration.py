"""Integration tests against real SPY put-chain data.

The fixture (``tests/fixtures/real_data/spy_2024_06_03.json``) was pulled from
``historical.flashalpha.com`` via ``scripts/fetch_real_data.py`` and pins down
how the simulator behaves against quotes that are not synthetic. Each test
documents one scenario; together they cover the entry primitive, the loop
wrapper, the patient exit, expiry settlement, the CSV provider round-trip,
and the gaps surfaced in the prior code review (NaN handling, naive-vs-aware
timestamps, ``bars_waited`` semantics under sparse providers).

The fixture is checked in so this suite runs without network access.
"""

from __future__ import annotations

import csv
import math
from datetime import date, datetime
from pathlib import Path

import pytest

from fillsim import (
    Config,
    CSVChainProvider,
    ExitReason,
    Leg,
    Spread,
    expiry_settlement_pnl,
    simulate_fill,
    simulate_fills,
    simulate_patient_exit,
)
from fillsim.filters import quote_passes_filter
from tests.fixtures.real_data_loader import RealDataset, load_real_dataset


@pytest.fixture(scope="module")
def real() -> RealDataset:
    return load_real_dataset()


def _put_credit_spread(
    short_strike: float,
    long_strike: float,
    limit: float,
    expiry: date,
) -> Spread:
    return Spread(
        short=Leg(strike=short_strike, bid=0.0, ask=0.0),
        long=Leg(strike=long_strike, bid=0.0, ask=0.0),
        limit_credit=limit,
        width=short_strike - long_strike,
        expiry=expiry,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — realistic limit fills within the wait window.
# ---------------------------------------------------------------------------


def test_real_chain_fills_525_520_pcs_at_realistic_credit(real: RealDataset) -> None:
    """Posting 525/520 PCS at 0.96 fills on the second bar (10:02).

    Inspecting the fixture: combo_bid at 10:01 is 0.88 (well below the
    0.98 needed to clear ``limit + fill_epsilon``); combo_bid at 10:02 is
    0.99, comfortably clearing. The limit is set with a clean float margin
    above the cross threshold so this scenario is robust to IEEE-754 rounding
    on the borderline. The simulator must fill at the posted limit, never
    better, even when bar mid is above the limit.
    """
    posted = datetime(2024, 6, 3, 10, 0)
    cand = _put_credit_spread(525.0, 520.0, 0.96, real.expiry)

    result = simulate_fills(posted, [cand], real.provider())

    assert result.filled
    fill = result.fill
    assert fill is not None
    assert fill.fill_ts == datetime(2024, 6, 3, 10, 2)
    assert fill.fill_price == pytest.approx(0.96)  # never better than the limit
    assert fill.mid_at_fill == pytest.approx(1.005, abs=1e-9)
    # edge_captured is negative when we fill below the bar's mid; that's a real
    # outcome on this chain — the limit was set just under combo_mid.
    assert fill.edge_captured == pytest.approx(-0.045, abs=1e-9)
    assert result.bars_waited == 2  # bars 10:01 and 10:02 walked


# ---------------------------------------------------------------------------
# Scenario 2 — unreachable limit produces no fill across the whole window.
# ---------------------------------------------------------------------------


def test_real_chain_unreachable_limit_never_fills(real: RealDataset) -> None:
    posted = datetime(2024, 6, 3, 10, 0)
    cand = _put_credit_spread(525.0, 520.0, 1.10, real.expiry)

    # combo_bid maxes at 1.03 across the whole 29-bar window; 1.10 is unreachable.
    result = simulate_fills(posted, [cand], real.provider())

    assert not result.filled
    assert result.fill is None
    assert result.near_misses == 0  # combo_bid never even touches the limit


# ---------------------------------------------------------------------------
# Scenario 3 — combo_bid touches the limit but does not clear epsilon.
# ---------------------------------------------------------------------------


def test_real_chain_near_miss_when_limit_equals_peak_combo_bid(real: RealDataset) -> None:
    posted = datetime(2024, 6, 3, 10, 0)
    # combo_bid peaks at 1.03 (10:10). With limit=1.03 and fill_epsilon=0.02,
    # we need combo_bid >= 1.05, which never happens. The 1.03 bar logs as a
    # near-miss; no other bar is high enough to even touch the limit.
    cand = _put_credit_spread(525.0, 520.0, 1.03, real.expiry)

    result = simulate_fills(posted, [cand], real.provider())

    assert not result.filled
    assert result.near_misses == 1


# ---------------------------------------------------------------------------
# Scenario 4 — per-bar primitive on a single real ChainSnapshot.
# ---------------------------------------------------------------------------


def test_real_chain_per_bar_primitive_returns_fill_event(real: RealDataset) -> None:
    snapshot = real.snapshot(datetime(2024, 6, 3, 10, 2))
    cand = _put_credit_spread(525.0, 520.0, 0.96, real.expiry)

    bar = simulate_fill(datetime(2024, 6, 3, 10, 2), snapshot, [cand])

    assert bar.fill is not None
    assert bar.fill.fill_price == pytest.approx(0.96)
    assert bar.fill.mid_at_fill == pytest.approx(1.005, abs=1e-9)
    assert bar.near_misses == 0


# ---------------------------------------------------------------------------
# Scenario 5 — sparse provider: bars_waited counts bars-with-data, NOT minutes.
# ---------------------------------------------------------------------------


def test_real_chain_sparse_bar_makes_bars_waited_underreport_wall_clock(
    real: RealDataset,
) -> None:
    """The fixture is missing 10:26 (404 from upstream). A limit posted at
    10:25 that fills at 10:27 has elapsed *two* wall-clock minutes, but
    ``bars_waited`` reports *one* — only the bars the provider returned
    are counted. This pins down the behaviour of ``FillResult.bars_waited``
    and surfaces the diagnostic gap when chains are sparse.
    """
    posted = datetime(2024, 6, 3, 10, 25)
    # combo_bid at 10:27 is exactly 0.90 in float; with limit=0.88 + eps=0.02
    # the threshold is 0.90 → fills cleanly on the first bar after the gap.
    cand = _put_credit_spread(525.0, 520.0, 0.88, real.expiry)

    result = simulate_fills(posted, [cand], real.provider())

    assert result.filled
    assert result.fill is not None
    assert result.fill.fill_ts == datetime(2024, 6, 3, 10, 27)
    assert result.bars_waited == 1  # only 10:27 walked — 10:26 is missing
    elapsed_minutes = int((result.fill.fill_ts - posted).total_seconds() // 60)
    assert elapsed_minutes == 2  # docs the contract gap: 1 bar seen, 2 mins elapsed
    assert result.near_misses == 0


# ---------------------------------------------------------------------------
# Scenario 6 — CSV provider round-trip on real data with naive timestamps.
# ---------------------------------------------------------------------------


def test_real_chain_csv_provider_matches_in_memory(
    real: RealDataset,
    tmp_path: Path,
) -> None:
    """Writing the fixture to CSV with naive ISO timestamps and loading back
    via CSVChainProvider must produce the same FillResult as the in-memory
    provider. Both providers are exercised with naive timestamps end-to-end.
    """
    csv_path = tmp_path / "real_chain.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "expiry", "strike", "right", "bid", "ask"])
        for bar in real.bars:
            for strike, (bid, ask) in bar.puts.items():
                w.writerow(
                    [
                        bar.ts.isoformat(),  # naive — no Z, no offset
                        real.expiry.isoformat(),
                        strike,
                        "PUT",
                        bid,
                        ask,
                    ]
                )

    posted = datetime(2024, 6, 3, 10, 0)
    cand = _put_credit_spread(525.0, 520.0, 0.97, real.expiry)

    csv_result = simulate_fills(posted, [cand], CSVChainProvider(csv_path))
    mem_result = simulate_fills(posted, [cand], real.provider())

    assert csv_result.filled
    assert mem_result.filled
    assert csv_result.fill is not None and mem_result.fill is not None
    assert csv_result.fill.fill_ts == mem_result.fill.fill_ts
    assert csv_result.fill.fill_price == mem_result.fill.fill_price
    assert csv_result.bars_waited == mem_result.bars_waited
    assert csv_result.near_misses == mem_result.near_misses


# ---------------------------------------------------------------------------
# Scenario 7 — CSV with Z timestamps + naive posted_ts is a contract gap.
# ---------------------------------------------------------------------------


def test_real_chain_csv_z_timestamp_clashes_with_naive_posted_ts(
    real: RealDataset,
    tmp_path: Path,
) -> None:
    """The CSV provider parses trailing-Z timestamps as tz-aware UTC. The
    README example posts naive datetimes. Mixing them inside ``simulate_fills``
    raises ``TypeError: can't compare offset-naive and offset-aware datetimes``
    inside the provider's range check. The README does not currently warn about
    this; this test pins the failure mode so it is hard to break silently.
    """
    csv_path = tmp_path / "real_chain_utc.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "expiry", "strike", "right", "bid", "ask"])
        for bar in real.bars:
            for strike, (bid, ask) in bar.puts.items():
                w.writerow(
                    [
                        f"{bar.ts.isoformat()}Z",  # tz-aware after parse
                        real.expiry.isoformat(),
                        strike,
                        "PUT",
                        bid,
                        ask,
                    ]
                )

    posted_naive = datetime(2024, 6, 3, 10, 0)  # naive — matches README
    cand = _put_credit_spread(525.0, 520.0, 0.97, real.expiry)
    provider = CSVChainProvider(csv_path)

    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        simulate_fills(posted_naive, [cand], provider)


# ---------------------------------------------------------------------------
# Scenario 8 — patient exit on a real path market-outs at deadline.
# ---------------------------------------------------------------------------


def test_real_chain_patient_exit_market_outs_after_pt_trigger(real: RealDataset) -> None:
    """Build a real (ts, combo_mid, combo_ask) path for the 525/520 PCS over
    the first seven minutes of the fixture. Entry credit 1.05 sits above
    every bar's mid, so PT triggers at bar 1 (10:01, mid 0.90 ≤ 0.945).
    The patient limit sits at 0.90; combo_ask never falls to 0.90 within the
    five-bar exit window, so the simulator market-outs at the deadline bar's
    combo_ask with reason ``PT_X``.
    """
    short_k, long_k = 525.0, 520.0
    path: list[tuple[datetime, float, float]] = []
    for bar in real.bars[:7]:
        s_bid, s_ask = bar.puts[short_k]
        l_bid, l_ask = bar.puts[long_k]
        combo_mid = (s_bid + s_ask) / 2.0 - (l_bid + l_ask) / 2.0
        combo_ask = s_ask - l_bid
        path.append((bar.ts, combo_mid, combo_ask))

    result = simulate_patient_exit(
        path,
        entry_credit=1.05,
        pt_frac=0.10,  # PT at 0.945
        sl_frac=0.50,  # SL at 1.575 — never trips
    )

    assert result is not None
    assert result.reason is ExitReason.PT_X
    assert result.close_ts == real.bars[6].ts  # market-out at deadline bar
    assert result.exit_credit is not None
    assert result.exit_credit == pytest.approx(path[6][2])  # combo_ask at deadline
    assert result.pnl_per_contract == pytest.approx(1.05 - path[6][2])


# ---------------------------------------------------------------------------
# Scenario 9 — expiry settlement using the real morning spot.
# ---------------------------------------------------------------------------


def test_real_chain_expiry_settlement_full_credit_when_spot_above_short(
    real: RealDataset,
) -> None:
    """At 10:00 ET, SPY mid is 527.93. A 525/520 put credit spread expiring
    that minute would settle at full credit kept (max profit), since spot is
    above the short strike. This validates the intrinsic-PnL helper against
    the real morning spot rather than a hand-picked synthetic value.
    """
    morning_spot = real.bars[0].spot
    assert morning_spot > 525.0
    pnl = expiry_settlement_pnl(
        spot=morning_spot,
        short_strike=525.0,
        long_strike=520.0,
        width=5.0,
        entry_credit=1.00,
    )
    assert pnl == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# Scenario 10 — Config respects the real chain's spread distribution.
# ---------------------------------------------------------------------------


def test_real_chain_tight_max_rel_spread_drops_wide_far_otm_legs(
    real: RealDataset,
) -> None:
    """The 530P at 10:00 has bid 3.19 / ask 3.94 — relative spread ≈ 0.21.
    A Config with ``fill_max_rel_spread`` set tighter than that should make
    candidates depending on the 530P invisible at this bar (no near miss,
    no fill — the leg is treated as missing).
    """
    bar0 = real.bars[0]
    bid_530, ask_530 = bar0.puts[530.0]
    rel = (ask_530 - bid_530) / ((ask_530 + bid_530) / 2.0)
    assert rel == pytest.approx(0.2105, abs=1e-3)

    snapshot = real.snapshot(bar0.ts)
    cand = _put_credit_spread(530.0, 525.0, 0.50, real.expiry)
    cfg_tight = Config(fill_max_rel_spread=0.10, min_edge_floor=-1.00)

    bar = simulate_fill(bar0.ts, snapshot, [cand], cfg_tight)
    assert bar.fill is None
    assert bar.near_misses == 0  # leg is invisible, not a near-miss


# ---------------------------------------------------------------------------
# Scenario 11 — NaN bid on a real chain currently bypasses the sanity gate.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "filters.quote_passes_filter does not reject non-finite bid/ask "
        "(code-review finding #1: NaN comparisons evaluate False, so "
        "(NaN, 0.73) passes the gate). With NaN on the long leg's bid, "
        "combo_bid is finite and may cross the limit while combo_mid is "
        "NaN — the simulator emits a FillEvent with mid_at_fill=NaN. When "
        "the gate is fixed (math.isfinite check), the leg is invisible and "
        "bar.fill is None."
    ),
)
def test_real_chain_nan_bid_should_be_rejected_but_currently_is_not(
    real: RealDataset,
) -> None:
    # Use 10:10 — a bar where 525/520 PCS combo_bid (1.76 - 0.73) is exactly
    # 1.03 in float, so the cross check is unambiguous regardless of NaN
    # handling on the *other* leg. Inject NaN on the long-leg BID, which keeps
    # combo_bid finite (it uses long-leg ask) but makes combo_mid_at_fill NaN.
    bar_ts = datetime(2024, 6, 3, 10, 10)
    snapshot = dict(real.snapshot(bar_ts))
    _, ask_520 = snapshot[(real.expiry, 520.0)]
    snapshot[(real.expiry, 520.0)] = (math.nan, ask_520)
    # Sanity: the filter as written today still accepts the malformed quote.
    assert quote_passes_filter(math.nan, ask_520, 0.50)

    cand = _put_credit_spread(525.0, 520.0, 0.99, real.expiry)
    bar = simulate_fill(bar_ts, snapshot, [cand])

    # Desired behaviour (passes after the fix): the NaN leg is treated as
    # missing, so no fill is produced.
    assert bar.fill is None
