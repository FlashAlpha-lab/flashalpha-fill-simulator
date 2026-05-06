"""Tests for ``simulate_patient_exit``."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from fillsim import Config, ExitReason, simulate_patient_exit

T0 = datetime(2026, 4, 15, 10, 5)


def _path(*entries: tuple[float, float]) -> list[tuple[datetime, float, float]]:
    """Build a path from (combo_mid, combo_ask) tuples; ts auto-increments by 1 min."""
    return [(T0 + timedelta(minutes=i), mid, ask) for i, (mid, ask) in enumerate(entries)]


def test_pt_limit_fills_on_trigger_bar_when_ask_already_at_limit():
    """If combo_ask <= trigger_mid on the trigger bar itself, fill instantly at limit."""
    path = _path((0.50, 0.50), (0.30, 0.28), (0.20, 0.20))  # trigger at idx=1, ask <= mid
    res = simulate_patient_exit(path, entry_credit=0.60, pt_frac=0.50, sl_frac=1.0)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.exit_credit == pytest.approx(0.30)
    assert res.pnl_per_contract == pytest.approx(0.30)


def test_pt_limit_fills_within_wait_window():
    """Trigger fires; ask drops to limit at idx+2 (within 5-bar window)."""
    path = _path(
        (0.50, 0.55),
        (0.30, 0.40),  # trigger here, ask=0.40 > mid=0.30, no fill yet
        (0.30, 0.35),
        (0.30, 0.30),  # ask now == limit (0.30) — fill here
        (0.30, 0.25),
    )
    res = simulate_patient_exit(path, entry_credit=0.60, pt_frac=0.50, sl_frac=1.0)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.exit_credit == pytest.approx(0.30)
    assert res.close_ts == path[3][0]


def test_pt_market_out_at_deadline():
    """Trigger fires; ask never drops to limit within wait window → market-out, pt_x."""
    path = _path(
        (0.50, 0.55),
        (0.30, 0.50),  # trigger
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),  # deadline (idx=6), still ask=0.50
    )
    cfg = Config(exit_max_wait_bars=5)
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.PT_X
    assert res.exit_credit == pytest.approx(0.50)


def test_sl_limit_fills():
    """SL trigger; combo_ask drops to mid → clean SL fill."""
    path = _path(
        (0.50, 0.55),
        (1.30, 1.30),  # SL: combo_mid = 1.30 >= entry * (1+SL_FRAC)
    )
    res = simulate_patient_exit(path, entry_credit=0.60, pt_frac=0.50, sl_frac=1.0)
    assert res is not None
    assert res.reason is ExitReason.SL
    assert res.exit_credit == pytest.approx(1.30)
    assert res.pnl_per_contract == pytest.approx(0.60 - 1.30)  # negative


def test_sl_market_out():
    path = _path(
        (0.50, 0.55),
        (1.30, 1.50),  # trigger; ask > mid
        (1.30, 1.50),
        (1.30, 1.50),
        (1.30, 1.50),
        (1.30, 1.50),
        (1.30, 1.50),  # deadline (idx=6)
    )
    cfg = Config(exit_max_wait_bars=5)
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.SL_X
    assert res.exit_credit == pytest.approx(1.50)


def test_no_trigger_returns_none():
    path = _path((0.55, 0.60), (0.50, 0.55), (0.45, 0.50))
    assert simulate_patient_exit(path, 0.60, 0.50, 1.0) is None


def test_pt_takes_priority_when_pt_and_sl_both_touch_same_bar():
    """When a single bar's mid satisfies both PT and SL thresholds, PT wins
    (PT is checked first in the trigger walk)."""
    # Construct entry_credit/PT/SL such that mid = 0.30 satisfies PT (≤ 0.30)
    # AND simultaneously satisfies SL (≥ ?). PT_FRAC=0.5, entry=0.60 → PT thresh=0.30.
    # For SL also tripping, SL_FRAC must be such that 0.60 * (1+SL_FRAC) <= 0.30,
    # i.e. SL_FRAC <= -0.50. The engine clamps SL_FRAC=0 to disable; only positive
    # SL_FRAC fires. So construct PT and SL to both fire at mid in a more natural way:
    # Use entry=0.10, SL_FRAC=2.0 → SL thresh=0.30. PT_FRAC=-something doesn't apply
    # (PT_FRAC must be positive). PT thresh = 0.10*(1-PT_FRAC). For PT to fire at
    # mid=0.30: PT thresh >= 0.30 → 0.10 * (1-PT_FRAC) >= 0.30 → PT_FRAC <= -2 ✗
    # Easier: artificially set entry_credit=1.0, PT_FRAC=0.7 (thresh=0.30), SL_FRAC=-0.7
    # SL_FRAC must be > 0 for SL to be active per code (`sl_frac > 0`). So SL=0.7,
    # entry=1.0 → SL thresh=1.7. mid would need to be both ≤ 0.30 (PT) AND ≥ 1.7 (SL),
    # impossible.
    # The "both touch" case is therefore NOT physically reachable in one bar — it's
    # actually a property of the threshold ordering. Document via this test.
    path = _path((0.30, 0.30))
    res = simulate_patient_exit(path, entry_credit=0.60, pt_frac=0.50, sl_frac=1.0)
    assert res is not None
    assert res.reason is ExitReason.PT  # only PT can trip first


def test_sl_disabled_when_sl_frac_zero():
    """sl_frac=0 disables SL entirely — only PT or expiry can close."""
    path = _path(
        (0.50, 0.55),
        (10.0, 10.0),  # combo_mid astronomic; would trigger any positive SL
        (0.30, 0.30),  # PT trigger here
    )
    res = simulate_patient_exit(path, entry_credit=0.60, pt_frac=0.50, sl_frac=0.0)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.close_ts == path[2][0]


def test_exit_mode_mid_closes_at_trigger_mid_no_walk():
    """exit_mode='mid' — close instantly at trigger combo_mid, ignore wait."""
    path = _path((0.50, 0.55), (0.30, 0.40), (0.30, 0.30))
    cfg = Config(exit_mode="mid")
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.exit_credit == pytest.approx(0.30)
    assert res.close_ts == path[1][0]  # trigger bar, not the later cheap-ask bar


def test_exit_mode_ask_closes_at_trigger_combo_ask():
    path = _path((0.50, 0.55), (0.30, 0.45), (0.30, 0.30))
    cfg = Config(exit_mode="ask")
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.exit_credit == pytest.approx(0.45)


def test_patient_limit_stays_fixed_at_trigger_mid_no_walk_down():
    """The patient-exit limit must NOT walk down over time.

    If the limit walked down, an ask in (orig_limit, new_limit] would fill.
    This test verifies the ask must be ≤ the original trigger mid for the
    whole window or we market-out.
    """
    # Trigger mid = 0.30. Ask oscillates around 0.30 but always > 0.30.
    # If the limit were walking down to e.g. 0.28, asks of 0.29 would fill —
    # we must NOT see that.
    path = _path(
        (0.50, 0.55),
        (0.30, 0.31),
        (0.30, 0.31),
        (0.30, 0.31),
        (0.30, 0.31),
        (0.30, 0.31),
        (0.30, 0.31),
    )
    cfg = Config(exit_max_wait_bars=5)
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.PT_X      # market-out, not fill
    assert res.exit_credit == pytest.approx(0.31)


def test_patient_wait_window_is_inclusive_of_deadline_bar():
    """If combo_ask drops to limit on the deadline bar exactly, that's a clean fill, not pt_x."""
    cfg = Config(exit_max_wait_bars=5)
    path = _path(
        (0.50, 0.55),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.50),
        (0.30, 0.30),  # deadline (idx=6 = trigger_idx 1 + 5), ask == limit
    )
    res = simulate_patient_exit(path, 0.60, 0.50, 1.0, cfg)
    assert res is not None
    assert res.reason is ExitReason.PT
    assert res.exit_credit == pytest.approx(0.30)
